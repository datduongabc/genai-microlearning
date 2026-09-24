"""Lệnh khởi động trong backend/Dockerfile.

Hỏng ở đây không làm backend sập, nó làm backend âm thầm chạy mã cũ: bộ tự nạp lại chết
đi mà tiến trình phục vụ vẫn trả lời bình thường, nên sửa tệp xong gọi API vẫn ra kết quả
của bản trước, và người ta kết luận sai về chính mã mình vừa viết.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"


def _lenh_khoi_dong() -> list[str]:
    khop = re.findall(r"^CMD\s+(\[.*\])\s*$", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(khop) == 1, "backend/Dockerfile phải có đúng một CMD dạng JSON"
    return json.loads(khop[0])


def test_tu_nap_lai_chi_theo_doi_ma_ung_dung():
    """Không giới hạn thì bộ theo dõi phủ cả /app, gồm .venv với khoảng 34 nghìn tệp thư viện
    so với 116 tệp mã, và đã chết vì hết chỗ theo dõi (os error 12) trong khi backend vẫn
    phục vụ, tức là vẫn chạy mã cũ mà không có gì báo."""
    lenh = _lenh_khoi_dong()
    if "--reload" not in lenh:
        return

    thu_muc = [lenh[i + 1] for i, tham_so in enumerate(lenh[:-1]) if tham_so == "--reload-dir"]
    assert thu_muc, "có --reload thì phải giới hạn bằng --reload-dir"
    assert all(d.strip("./") == "app" for d in thu_muc), f"chỉ được theo dõi thư mục app/, đang theo dõi {thu_muc}"


def test_tu_nap_lai_do_dinh_ky_chu_khong_cho_su_kien_tu_he_dieu_hanh():
    """Mã được gắn vào container qua bind mount từ ổ đĩa Windows, và sự kiện "tệp bị sửa" không
    đi qua được lớp đó; chỉ sự kiện tạo và xoá tệp là đi qua. Bộ tự nạp lại chờ sự kiện thì
    thấy tệp mới nhưng không thấy tệp vừa sửa, tức backend vẫn chạy mã cũ sau mỗi lần sửa."""
    if "--reload" not in _lenh_khoi_dong():
        return

    env = re.findall(r"^ENV\s+(.+)$", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE)
    gia_tri = {k: v for dong in env for k, _, v in [dong.strip().partition("=")]}

    assert gia_tri.get("WATCHFILES_FORCE_POLLING", "").lower() == "true"
