import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from app.api import documents
from app.api.documents import MAX_UPLOAD_SIZE_BYTES
from app.config import settings
from app.db import SessionLocal
from app.ingestion import document_readers
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.ingestion.document_readers import SUPPORTED_EXTENSIONS, InputLimitError
from app.main import app
from app.models import Document, Job
from app.storage import upload_store
from tests.auth_helpers import create_and_login
from tests.upload_helpers import upload_document

client = TestClient(app)


def _create_and_login(email: str) -> str:
    return create_and_login(client, email)["access_token"]


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

    response = upload_document(
        client, course_id, "virus.exe", b"not a document", {"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_upload_rejects_oversized_file():
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-size-{suffix}@example.com")
    course_id = _make_course(token, suffix)

    oversized = b"x" * (MAX_UPLOAD_SIZE_BYTES + 1)
    response = upload_document(client, course_id, "big.html", oversized, {"Authorization": f"Bearer {token}"})
    assert response.status_code == 413


def test_a_body_larger_than_the_declared_name_allows_is_cut_off_while_streaming(tmp_path, monkeypatch):
    # The declared size is the client's word; the bytes that arrive are checked as they come.
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-lie-{suffix}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    course_id = _make_course(token, suffix)
    started = client.post(
        "/documents/uploads", params={"course_id": course_id}, json={"filename": "a.html", "size": 10}, headers=headers,
    ).json()

    response = client.put(started["upload"]["url"], content=b"x" * (MAX_UPLOAD_SIZE_BYTES + 1), headers=headers)

    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == [], "the part written must be removed"
    assert client.post(f"/documents/{started['document_id']}/complete", headers=headers).status_code == 409


# --- định dạng cũ cần LibreOffice ---------------------------------------------
# Ảnh backend không cài LibreOffice, nên các định dạng này từng được nhận lúc upload rồi
# hỏng trong job nền, và giảng viên chỉ biết khi vào trang theo dõi lỗi. Từ chối ngay lúc
# upload, kèm cách sửa, thì giảng viên biết phải làm gì trong vài giây.

DINH_DANG_CU = {".ppt": ".pptx", ".odp": ".pptx", ".doc": ".docx", ".odt": ".docx", ".rtf": ".docx"}


def _tai_len(ten_tep: str):
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-cu-{suffix}@example.com")
    course_id = _make_course(token, suffix)
    phan_hoi = upload_document(
        client, course_id, ten_tep, b"noi dung bat ky", {"Authorization": f"Bearer {token}"},
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

    trang = Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "CourseDetailPage.tsx"
    khop = re.findall(r'accept="([^"]+)"', trang.read_text(encoding="utf-8"))

    assert len(khop) == 1, "mong đợi đúng một ô chọn tệp trên trang tài liệu môn học"
    assert set(khop[0].split(",")) == SUPPORTED_EXTENSIONS


# --- giới hạn đã chốt -----------------------------------------------------------
# Tài liệu 25 MB và 150 trang/slide, audio 200 MB, video 1 GB, audio/video 90 phút.
# Mỗi giới hạn được kiểm ở đúng biên: nhận ở giới hạn, từ chối ngay trên giới hạn.

MB = 1024 * 1024


@pytest.fixture
def upload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))
    suffix = uuid.uuid4().hex[:8]
    token = _create_and_login(f"upload-limit-{suffix}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = []

    def send(filename: str, content: bytes):
        # Mỗi lần tải dùng một môn riêng: môn đang có tài liệu chờ xử lý thì từ chối tải tiếp.
        course_id = _make_course(token, uuid.uuid4().hex[:8])
        response = upload_document(client, course_id, filename, content, headers)
        if response.status_code == 200:
            created.append(response.json()["id"])
        return response

    yield send
    # Job 'queued' sót lại làm sai các test claim job chạy sau.
    with SessionLocal() as db:
        db.query(Job).filter(Job.document_id.in_(created)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(created)).delete(synchronize_session=False)
        db.commit()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("pending-")], "tệp tạm phải được dọn"


def test_gioi_han_da_chot():
    assert documents.MAX_UPLOAD_SIZE_BYTES == 25 * MB
    assert documents.MAX_UPLOAD_SIZE_BYTES_AUDIO == 200 * MB
    assert documents.MAX_UPLOAD_SIZE_BYTES_VIDEO == 1024 * MB
    assert document_readers.MAX_DOCUMENT_PAGES == 150


def test_tai_lieu_25_mb_o_bien(upload):
    assert upload("a.html", b"x" * (25 * MB)).status_code == 200
    response = upload("b.html", b"x" * (25 * MB + 1))
    assert response.status_code == 413
    assert response.json()["detail"] == "Tài liệu tối đa 25 MB"


@pytest.mark.parametrize("name,constant,message", [
    ("a.mp3", "MAX_UPLOAD_SIZE_BYTES_AUDIO", "Tệp âm thanh tối đa 200 MB"),
    ("a.mp4", "MAX_UPLOAD_SIZE_BYTES_VIDEO", "Tệp video tối đa 1 GB"),
])
def test_audio_video_theo_byte_o_bien(upload, monkeypatch, name, constant, message):
    # Gửi 200 MB / 1 GB qua TestClient quá chậm: thu nhỏ giới hạn để kiểm phép so sánh ở biên,
    # còn con số thật đã được test_gioi_han_da_chot kiểm.
    monkeypatch.setattr(documents, constant, 1000)
    monkeypatch.setattr(document_readers, "_duration_seconds", lambda path: 60)
    assert upload(name, b"x" * 1000).status_code == 200
    response = upload(name, b"x" * 1001)
    assert response.status_code == 413
    assert response.json()["detail"] == message


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def _pptx(slides: int, tmp_path) -> bytes:
    deck = Presentation()
    for _ in range(slides):
        deck.slides.add_slide(deck.slide_layouts[6])
    path = tmp_path / f"deck-{slides}.pptx"
    deck.save(path)
    return path.read_bytes()


# Số trang và thời lượng cần chính tệp, nên được kiểm ở bước extract của worker chứ không lúc
# upload: tệp có thể chỉ nằm trên S3. Quá giới hạn là lỗi cuối (không thử lại), tài liệu báo
# lỗi kèm lý do, và tệp bị xoá.

def _limit_error(response) -> InputLimitError | None:
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        job = db.get(Job, response.json()["job_id"])
    try:
        run_document_pipeline_step(job)
    except InputLimitError as error:
        assert upload_store().size(job.payload["storage_key"]) is None, "a refused file has no further use"
        return error
    except Exception:
        return None
    return None


def test_pdf_150_trang_o_bien(upload):
    assert _limit_error(upload("a.pdf", _pdf(150))) is None
    error = _limit_error(upload("b.pdf", _pdf(151)))
    assert error is not None and "151 trang" in str(error)


def test_pptx_150_slide_o_bien(upload, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("decks")
    assert _limit_error(upload("a.pptx", _pptx(150, tmp))) is None
    error = _limit_error(upload("b.pptx", _pptx(151, tmp)))
    assert error is not None and "151 slide" in str(error)


@pytest.mark.parametrize("name", ["a.mp3", "a.mp4"])
def test_audio_video_90_phut_o_bien(upload, monkeypatch, name):
    # Cùng phép kiểm với reader: 5400 s cộng 1 s đệm của bộ mã hoá MP3.
    monkeypatch.setattr(document_readers, "_duration_seconds", lambda path: 5401)
    assert _limit_error(upload(name, b"input")) is None
    monkeypatch.setattr(document_readers, "_duration_seconds", lambda path: 5401.5)
    error = _limit_error(upload(name, b"input"))
    assert error is not None and "90 phút" in str(error)


@pytest.mark.parametrize("duoi", [".srt", ".vtt", ".txt"])
def test_dinh_dang_da_bo_bi_tu_choi(upload, duoi):
    response = upload(f"bai-giang{duoi}", b"1\n00:00:00,000 --> 00:00:01,000\nXin chao\n")
    assert response.status_code == 422
    assert "không được hỗ trợ" in response.json()["detail"]
