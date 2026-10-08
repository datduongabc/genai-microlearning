"""Danh sách model mà giảng viên được chọn, hỏi chính gateway chứ không ghi cứng ở đây.

Thêm một model là sửa devops/gateway/config.yaml rồi tạo lại container gateway, backend
không phải sửa gì. Đó cũng là tính chất mà app/config.py đã tuyên bố: đổi nhà cung cấp
hay đổi model là việc của cấu hình, không phải của mã.

Gateway tự báo `mode` cho từng alias (litellm suy ra từ bảng model của nó), nên việc loại
các alias nhúng khỏi danh sách không cần khai thêm gì. Nhãn hiển thị thì phải khai, dưới
khóa `model_info` trong cấu hình gateway.
"""

import threading
import time
from dataclasses import dataclass

import httpx
import litellm
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

    def __init__(self, alias: str, ly_do: str):
        super().__init__(f"{alias}: {ly_do}")
        self.alias = alias
        self.ly_do = ly_do


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
    chon = []
    for muc in raw:
        thong_tin = muc.get("model_info") or {}
        # Chỉ nhận đúng mode "chat". Một alias không rõ mode thà bỏ sót còn hơn đem ra cho
        # chọn: bỏ sót thì giảng viên thấy thiếu và báo, còn chọn nhầm một model nhúng thì
        # cả pipeline sinh nội dung chết bằng một lỗi chẳng liên quan gì tới việc chọn model.
        if thong_tin.get("mode") != "chat":
            continue
        alias = muc["model_name"]
        # Nhà cung cấp là tiền tố của tên model, nên thêm model mới không phải khai lại nó.
        # Groq đi qua litellm dưới dạng "groq/openai/..." nên chỉ lấy đoạn đầu.
        provider = str(muc.get("litellm_params", {}).get("model", "")).split("/")[0]
        chon.append(ModelChoice(
            alias=alias,
            label=thong_tin.get("display_name") or alias,
            provider=provider,
            note=thong_tin.get("note"),
        ))
    # Thứ tự cố định để danh sách trên giao diện không đổi chỗ giữa hai lần tải.
    return sorted(chon, key=lambda c: (c.provider, c.alias))


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
    except httpx.HTTPError as loi:
        # Không ghi đệm kết quả lỗi: gateway vừa lên lại thì request kế tiếp phải thấy ngay.
        raise GatewayUnavailable(str(loi)) from loi
    chon = to_choices(raw)
    with _lock:
        _cache = (time.monotonic(), chon)
    return chon


def is_available(alias: str) -> bool:
    return any(c.alias == alias for c in available_models())


def probe(alias: str) -> None:
    """Thử một lệnh một token tới đúng model đã chọn, ngay lúc bấm sinh.

    Có trong danh sách chưa chắc đã dùng được, vì gateway liệt kê mọi alias dù khóa của
    nhà cung cấp đang trống. Không thử thì lượt sinh chạy mười phút rồi mới hỏng; thử thì
    giảng viên biết ngay và chọn model khác. Một token là đủ để biết khóa và số dư còn
    dùng được, mà không tốn hạn mức đáng kể.

    Bắt theo lớp cha của openai chứ không phải của litellm, và đó không phải tuỳ tiện:
    litellm.Timeout KHÔNG kế thừa litellm.APIConnectionError, và litellm.AuthenticationError
    KHÔNG kế thừa litellm.APIError. Bắt theo litellm thì quá hạn chờ bị xếp nhầm thành
    "model hỏng", còn lỗi khóa lọt qua câu bắt tổng quát.
    """
    try:
        litellm.completion(
            model=f"openai/{alias}",
            api_base=settings.llm_gateway_url,
            api_key=settings.llm_gateway_key,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    # Trước tiên: gateway không với tới được thì mọi model đều hỏng như nhau, nên đổi model
    # không giúp gì. Phải đứng trước câu bắt tổng quát vì nó cũng là một openai.APIError.
    except openai.APIConnectionError as loi:
        raise GatewayUnavailable(str(loi)) from loi
    except openai.APIError as loi:
        raise ModelUnusable(alias, _ly_do(loi)) from loi


# Từ khoá lấy từ thông báo lỗi thật của gateway, đo trên chính stack này: khóa OpenAI
# trống thì gateway trả 500 mang "api_key" và "credential" (nó hỏng ngay lúc dựng request,
# nên KHÔNG ra 401 như người ta hay tưởng); khóa Anthropic chưa nạp tiền thì trả 400 mang
# "credit", "balance", "billing".
_TU_KHOA_KHOA = ("api_key", "api key", "credential", "unauthorized", "authentication")
_TU_KHOA_HAN_MUC = ("rate limit", "quota", "resource_exhausted")
_TU_KHOA_SO_DU = ("credit", "balance", "billing")


def _ly_do(loi: openai.APIError) -> str:
    """Câu giải thích cho giảng viên. Chỉ quyết định lời văn, không quyết định mã trả về:
    phân loại 422 hay 503 đã xong ở trên theo loại ngoại lệ, không theo chuỗi, vì chuỗi
    của nhà cung cấp có thể đổi bất cứ lúc nào.

    Không khớp từ khoá nào thì trả một câu không đoán nguyên nhân. Đoán sai còn tệ hơn
    không nói: bảo "hết số dư" khi thực ra là thiếu khóa sẽ khiến người ta đi nạp tiền."""
    van_ban = str(loi).lower()
    if isinstance(loi, AuthenticationError) or any(k in van_ban for k in _TU_KHOA_KHOA):
        return "chưa có API key hợp lệ cho nhà cung cấp của model này"
    if isinstance(loi, RateLimitError) or any(k in van_ban for k in _TU_KHOA_HAN_MUC):
        return "đã hết hạn mức hoặc đang bị giới hạn tốc độ, thử lại sau"
    if any(k in van_ban for k in _TU_KHOA_SO_DU):
        return "tài khoản của nhà cung cấp đã hết số dư"
    return "nhà cung cấp chưa phục vụ được model này"
