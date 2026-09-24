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

from app.config import settings

# Hỏi lại gateway sau mỗi khoảng này. Có thời hạn thay vì nhớ mãi: sửa cấu hình gateway
# rồi tạo lại nó thì backend phải thấy model mới mà không cần khởi động lại backend.
CACHE_SECONDS = 60
REQUEST_TIMEOUT = 10.0


class GatewayUnavailable(Exception):
    """Gateway không trả lời được danh sách model. Là ngoại lệ riêng chứ không để lỗi httpx
    lọt ra ngoài: tầng API đổi nó thành 503 ở một chỗ duy nhất (app/main.py), vì 500 kèm
    traceback đọc như lỗi của backend trong khi thực ra chỉ là gateway chưa lên."""


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
