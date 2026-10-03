import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.documents import MAX_UPLOAD_SIZE_BYTES
from app.ingestion.document_readers import SUPPORTED_EXTENSIONS
from app.main import app
from tests.auth_helpers import STRONG_PASSWORD, make_account

client = TestClient(app)


def _create_and_login(email: str) -> str:
    make_account(email, name="Test")
    response = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    return response.json()["access_token"]


def _make_course(token: str, suffix: str) -> str:
    course = client.post(
        "/courses", json={"name": f"Upload validation test {suffix}"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    return course["id"]


def test_upload_rejects_unsupported_extension():
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-ext-{suffix}@example.com")
    course_id = _make_course(token, suffix)

    response = client.post(
        "/documents", params={"course_id": course_id},
        files={"file": ("virus.exe", b"not a document", "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_upload_rejects_oversized_file():
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-size-{suffix}@example.com")
    course_id = _make_course(token, suffix)

    oversized = b"x" * (MAX_UPLOAD_SIZE_BYTES + 1)
    response = client.post(
        "/documents", params={"course_id": course_id},
        files={"file": ("big.txt", oversized, "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 413


# --- định dạng cũ cần LibreOffice ---------------------------------------------
# Ảnh backend không cài LibreOffice, nên các định dạng này từng được nhận lúc upload rồi
# hỏng trong job nền, và giảng viên chỉ biết khi vào trang theo dõi lỗi. Từ chối ngay lúc
# upload, kèm cách sửa, thì giảng viên biết phải làm gì trong vài giây.

DINH_DANG_CU = {".ppt": ".pptx", ".odp": ".pptx", ".doc": ".docx", ".odt": ".docx", ".rtf": ".docx"}


def _tai_len(ten_tep: str):
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-cu-{suffix}@example.com")
    course_id = _make_course(token, suffix)
    phan_hoi = client.post(
        "/documents", params={"course_id": course_id},
        files={"file": (ten_tep, b"noi dung bat ky", "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    tai_lieu = client.get("/documents", params={"course_id": course_id}, headers={"Authorization": f"Bearer {token}"})
    assert tai_lieu.status_code == 200, "không đọc được danh sách thì phép kiểm \"không tạo tài liệu\" vô nghĩa"
    return phan_hoi, tai_lieu


@pytest.mark.parametrize("duoi,duoi_moi", sorted(DINH_DANG_CU.items()))
def test_dinh_dang_cu_bi_tu_choi_ngay_luc_upload_va_chi_cach_sua(duoi, duoi_moi):
    phan_hoi, tai_lieu = _tai_len(f"Chuong 1{duoi}")

    assert phan_hoi.status_code == 422
    assert duoi_moi in phan_hoi.json()["detail"], "phải chỉ ra định dạng nên lưu lại thành"
    assert tai_lieu.json() == [], "bị từ chối thì không được tạo tài liệu hay job nào"


def test_dinh_dang_la_thi_bao_nhung_dinh_dang_duoc_nhan():
    phan_hoi, _ = _tai_len("bang-tinh.xlsx")

    assert phan_hoi.status_code == 422
    assert ".pdf" in phan_hoi.json()["detail"] and ".pptx" in phan_hoi.json()["detail"]


def test_danh_sach_dinh_dang_nhan_khong_con_dinh_dang_nao_can_libreoffice():
    """Mọi định dạng được nhận phải đọc được bằng thứ đã có trong ảnh backend."""
    assert not (SUPPORTED_EXTENSIONS & set(DINH_DANG_CU))


def test_o_chon_tep_tren_giao_dien_nhan_dung_cac_dinh_dang_backend_nhan():
    """Thuộc tính accept của ô chọn tệp là bản sao thứ hai của danh sách này. Lệch về phía nhận
    nhiều hơn thì giảng viên chọn được tệp để rồi bị từ chối; lệch về phía ít hơn thì một định
    dạng backend đọc được lại không chọn được trong hộp thoại."""
    import re
    from pathlib import Path

    trang = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "CourseDetailPage.tsx"
    khop = re.findall(r'accept="([^"]+)"', trang.read_text(encoding="utf-8"))

    assert len(khop) == 1, "mong đợi đúng một ô chọn tệp trên trang tài liệu môn học"
    assert set(khop[0].split(",")) == SUPPORTED_EXTENSIONS
