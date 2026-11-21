"""Danh sách model mà giảng viên được chọn, hỏi chính gateway chứ không ghi cứng ở đây.

Thêm một model là sửa devops/gateway/config.yaml rồi tạo lại container gateway, backend
không phải sửa gì. Đó cũng là tính chất mà app/config.py đã tuyên bố: đổi nhà cung cấp
hay đổi model là việc của cấu hình, không phải của mã.

Gateway tự báo `mode` cho từng alias (litellm suy ra từ bảng model của nó), nên việc loại
các alias nhúng khỏi danh sách không cần khai thêm gì. Nhãn hiển thị thì phải khai, dưới
khóa `model_info` trong cấu hình gateway.
"""

import re
import threading
import time
from dataclasses import dataclass

import httpx
import openai
from litellm.exceptions import AuthenticationError, RateLimitError

from app.config import settings

# Hỏi lại gateway sau mỗi khoảng này. Có thời hạn thay vì nhớ mãi: sửa cấu hình gateway
# rồi tạo lại nó thì backend phải thấy model mới mà không cần khởi động lại backend.
CACHE_SECONDS = 60
REQUEST_TIMEOUT = 10.0


class GatewayUnavailable(Exception):
    """Gateway không trả lời được danh sách model. Là ngoại lệ riêng chứ không để lỗi httpx
    lọt ra ngoài: tầng API đổi nó thành 503 ở một chỗ duy nhất (app/main.py), vì 500 kèm
    traceback đọc như lỗi của backend trong khi thực ra chỉ là gateway chưa lên."""


class ModelUnusable(Exception):
    """Gateway trả lời được, nhưng model này không phục vụ được: khóa của nhà cung cấp
    trống hoặc sai, hết số dư, hết hạn mức ngày. Khác với GatewayUnavailable ở chỗ đổi
    sang model khác thì có thể được, nên thông báo phải bảo giảng viên làm đúng điều đó."""

    def __init__(self, alias: str, reason: str):
        super().__init__(f"{alias}: {reason}")
        self.alias = alias
        self.reason = reason


@dataclass(frozen=True)
class ModelChoice:
    alias: str
    label: str
    provider: str
    note: str | None = None


def fetch_model_info() -> list[dict]:
    response = httpx.get(
        f"{settings.llm_gateway_url.rstrip('/')}/model/info",
        headers={"Authorization": f"Bearer {settings.llm_gateway_key}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["data"]


def to_choices(raw: list[dict]) -> list[ModelChoice]:
    choices = []
    for item in raw:
        info = item.get("model_info") or {}
        # Chỉ nhận đúng mode "chat". Một alias không rõ mode thà bỏ sót còn hơn đem ra cho
        # chọn: bỏ sót thì giảng viên thấy thiếu và báo, còn chọn nhầm một model nhúng thì
        # cả pipeline sinh nội dung chết bằng một lỗi chẳng liên quan gì tới việc chọn model.
        if info.get("mode") != "chat" or info.get("hidden"):
            continue
        alias = item["model_name"]
        # Nhà cung cấp là tiền tố của tên model, nên thêm model mới không phải khai lại nó.
        # Groq đi qua litellm dưới dạng "groq/openai/..." nên chỉ lấy đoạn đầu. Mọi model trả
        # phí đi qua OpenRouter ("openrouter/<hãng>/<model>"), nên với nó lấy hãng ở đoạn thứ
        # hai, kẻo cả danh sách gom về một nhóm "openrouter".
        parts = str(item.get("litellm_params", {}).get("model", "")).split("/")
        provider = parts[1] if parts[0] == "openrouter" and len(parts) >= 3 else parts[0]
        choices.append(ModelChoice(
            alias=alias,
            label=info.get("display_name") or alias,
            provider=provider,
            note=info.get("note"),
        ))
    # Thứ tự cố định để danh sách trên giao diện không đổi chỗ giữa hai lần tải.
    return sorted({c.alias: c for c in choices}.values(), key=lambda c: (c.provider, c.alias))


_lock = threading.Lock()
_cache: tuple[float, list[ModelChoice]] | None = None


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def available_models() -> list[ModelChoice]:
    global _cache
    with _lock:
        if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
    # Gọi mạng ngoài lock: giữ lock suốt một request chậm sẽ chặn mọi request khác cùng
    # lúc, mà cái giá phải trả nếu hai request cùng vào là một lần gọi gateway thừa.
    try:
        raw = fetch_model_info()
    except httpx.HTTPError as error:
        # Không ghi đệm kết quả lỗi: gateway vừa lên lại thì request kế tiếp phải thấy ngay.
        raise GatewayUnavailable(str(error)) from error
    choices = to_choices(raw)
    with _lock:
        _cache = (time.monotonic(), choices)
    return choices


def is_available(alias: str) -> bool:
    return any(c.alias == alias for c in available_models())


def served_or_default(alias: str | None) -> str | None:
    """A stored alias (a row, a queued job) the gateway has since dropped runs on the configured model, so a retry
    or an edit of old work does not fail on it. A gateway that does not answer cannot tell: the alias is kept, and
    the call itself then fails as the outage it is."""
    if alias is None:
        return None
    try:
        return alias if is_available(alias) else settings.llm_model
    except GatewayUnavailable:
        return alias


def probe(alias: str) -> None:
    """Thử một lệnh một token tới đúng model đã chọn, ngay lúc bấm sinh.

    Có trong danh sách chưa chắc đã dùng được, vì gateway liệt kê mọi alias dù khóa của
    nhà cung cấp đang trống. Không thử thì lượt sinh chạy mười phút rồi mới hỏng; thử thì
    giảng viên biết ngay lỗi khóa hoặc số dư và chọn model khác. Quy tắc chấp nhận lỗi
    quota để dùng fallback nằm trong docs/gateway-routing.md.

    Bắt theo lớp cha của openai chứ không phải của litellm, và đó không phải tuỳ tiện:
    litellm.Timeout KHÔNG kế thừa litellm.APIConnectionError, và litellm.AuthenticationError
    KHÔNG kế thừa litellm.APIError. Bắt theo litellm thì quá hạn chờ bị xếp nhầm thành
    "model hỏng", còn lỗi khóa lọt qua câu bắt tổng quát.
    """
    try:
        from app.llm.gateway import _completion, using_model

        with using_model(alias):
            _completion(
                messages=[{"role": "user", "content": "ping"}], max_tokens=1,
                # One token answers in seconds; the lecturer waits on it behind CloudFront's 60 s.
                extra_body={"disable_fallbacks": True}, timeout=20,
            )
    # Trước tiên: gateway không với tới được thì mọi model đều hỏng như nhau, nên đổi model
    # không giúp gì. Phải đứng trước câu bắt tổng quát vì nó cũng là một openai.APIError.
    except openai.APIConnectionError as error:
        raise GatewayUnavailable(str(error)) from error
    except openai.APIError as error:
        # Quota alone does not make the alias unusable: generation can use its backups.
        if error.status_code == 429 and not _BALANCE_ERROR.search(str(error)):
            return
        raise ModelUnusable(alias, _reason(error)) from error


# Từ khoá lấy từ thông báo lỗi thật của gateway, đo trên chính stack này: khóa OpenAI
# trống thì gateway trả 500 mang "api_key" và "credential" (nó hỏng ngay lúc dựng request,
# nên KHÔNG ra 401 như người ta hay tưởng); khóa Anthropic chưa nạp tiền thì trả 400 mang
# "credit", "balance", "billing".
_KEY_WORDS = ("api_key", "api key", "credential", "unauthorized", "authentication")
_QUOTA_WORDS = ("rate limit", "quota", "resource_exhausted")
# Only explicit credit signals: Gemini's free-tier quota 429 also says "plan and billing details".
_BALANCE_ERROR = re.compile(
    r"\b(?:insufficient_quota|billing_hard_limit_reached|credits?|insufficient[ _]funds)\b",
    re.IGNORECASE,
)


def out_of_credit(text: str) -> bool:
    """Whether a provider's error says the account has no credit left (OpenRouter's 402 among them)."""
    return bool(_BALANCE_ERROR.search(text))


def _reason(error: openai.APIError) -> str:
    """Câu giải thích cho giảng viên, sau khi probe đã phân loại lỗi.

    Không khớp từ khoá nào thì trả một câu không đoán nguyên nhân. Đoán sai còn tệ hơn
    không nói: bảo "hết số dư" khi thực ra là thiếu khóa sẽ khiến người ta đi nạp tiền."""
    text = str(error).lower()
    if isinstance(error, AuthenticationError) or any(k in text for k in _KEY_WORDS):
        return "chưa có API key hợp lệ cho nhà cung cấp của model này"
    if _BALANCE_ERROR.search(text):
        return "tài khoản của nhà cung cấp đã hết số dư"
    if isinstance(error, RateLimitError) or any(k in text for k in _QUOTA_WORDS):
        return "đã hết hạn mức hoặc đang bị giới hạn tốc độ, thử lại sau"
    return "nhà cung cấp chưa phục vụ được model này"
