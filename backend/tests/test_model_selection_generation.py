"""Model giảng viên chọn đi được từ request tới lượt sinh chạy trong worker.

Request và worker là hai tiến trình khác nhau, request đã trả về từ lâu trước khi worker
bắt đầu, nên alias phải nằm trong payload của job chứ không thể giữ trong bộ nhớ.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.llm import catalog
from app.llm import gateway as llm
from app.main import app
from app.models import (
    Course,
    Document,
    DocumentKind,
    DocumentStatus,
    Job,
    JobType,
    Module,
    User,
    UserRole,
)

client = TestClient(app)

GATEWAY_GIA_LAP = [
    {
        "model_name": "gemini-fast",
        "litellm_params": {"model": "gemini/gemini-3.5-flash-lite"},
        "model_info": {"mode": "chat"},
    },
    {
        "model_name": "groq-pro",
        "litellm_params": {"model": "groq/openai/gpt-oss-120b"},
        "model_info": {"mode": "chat"},
    },
]


da_thu: list[str] = []


@pytest.fixture(autouse=True)
def moi_truong(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(catalog, "fetch_model_info", lambda: GATEWAY_GIA_LAP)
    # Mặc định là model thử được. Test nào cần phép thử hỏng thì tự đặt lại, và ghi lại
    # các alias đã được thử để kiểm được đường mặc định không hề gọi phép thử.
    da_thu.clear()
    monkeypatch.setattr(catalog, "probe", lambda alias: da_thu.append(alias))
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def _tai_lieu_cua_giang_vien(mac_dinh: str | None = None) -> tuple[str, uuid.UUID]:
    """Trả về (token, document_id) của một giảng viên có sẵn một tài liệu đã parse."""
    db = SessionLocal()
    try:
        user = User(
            email=f"gen-{uuid.uuid4().hex[:10]}@example.com", name="T", hashed_password="x",
            role=UserRole.teacher, default_model_alias=mac_dinh,
        )
        db.add(user)
        db.flush()
        course = Course(name="C", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(
            course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded,
            kind=DocumentKind.lecture, raw_text="noi dung da parse",
        )
        db.add(doc)
        db.commit()
        return create_access_token(str(user.id)), doc.id
    finally:
        db.close()


def _payload_cua_job(document_id: uuid.UUID) -> dict:
    db = SessionLocal()
    try:
        job = (
            db.query(Job)
            .filter(Job.document_id == document_id, Job.job_type == JobType.module_generation)
            .one()
        )
        return job.payload
    finally:
        db.close()


def _so_job(document_id: uuid.UUID) -> int:
    db = SessionLocal()
    try:
        return db.query(Job).filter(Job.document_id == document_id).count()
    finally:
        db.close()


def test_alias_chon_luc_bam_sinh_di_vao_payload_cua_job():
    token, doc_id = _tai_lieu_cua_giang_vien()

    phan_hoi = client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "groq-pro"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert phan_hoi.status_code == 200, phan_hoi.text
    assert _payload_cua_job(doc_id)["model_alias"] == "groq-pro"


def test_khong_chon_gi_thi_dung_mac_dinh_cua_giang_vien():
    token, doc_id = _tai_lieu_cua_giang_vien(mac_dinh="groq-pro")

    client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert _payload_cua_job(doc_id)["model_alias"] == "groq-pro"


def test_mac_dinh_da_luu_ma_gateway_da_bo_thi_sinh_bang_cau_hinh_he_thong():
    """gemini-pro đã đổi tên thành gemini-flash, nhưng database có thể vẫn lưu tên cũ."""
    token, doc_id = _tai_lieu_cua_giang_vien(mac_dinh="gemini-pro")

    phan_hoi = client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert phan_hoi.status_code == 200, phan_hoi.text
    assert "model_alias" not in _payload_cua_job(doc_id)
    assert da_thu == []


def test_mac_dinh_da_luu_ma_gateway_da_bo_thi_danh_sach_bao_mac_dinh_he_thong():
    token, _ = _tai_lieu_cua_giang_vien(mac_dinh="gemini-pro")

    body = client.get("/models", headers={"Authorization": f"Bearer {token}"}).json()

    assert body["default"] == settings.llm_model
    assert body["default"] in [m["alias"] for m in body["models"]]


def test_chon_luc_bam_sinh_thang_mac_dinh_cua_giang_vien():
    token, doc_id = _tai_lieu_cua_giang_vien(mac_dinh="groq-pro")

    client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "gemini-fast"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert _payload_cua_job(doc_id)["model_alias"] == "gemini-fast"


def test_khong_chon_va_khong_co_mac_dinh_thi_payload_khong_ghi_alias_nao():
    """Để trống chứ không ghi sẵn giá trị từ cấu hình: ghi sẵn thì đổi cấu hình hệ thống
    sẽ không có tác dụng với các job đã nằm trong hàng đợi."""
    token, doc_id = _tai_lieu_cua_giang_vien()

    client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert "model_alias" not in _payload_cua_job(doc_id)


def test_alias_gateway_khong_phuc_vu_thi_tu_choi_va_khong_tao_job():
    token, doc_id = _tai_lieu_cua_giang_vien()

    phan_hoi = client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "khong-ton-tai"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert phan_hoi.status_code == 422
    assert _so_job(doc_id) == 0


def test_worker_chay_luot_sinh_bang_dung_model_trong_payload(monkeypatch):
    from app.generation import module_generation_pipeline as pipeline

    token, doc_id = _tai_lieu_cua_giang_vien()
    client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "groq-pro"},
        headers={"Authorization": f"Bearer {token}"},
    )
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.document_id == doc_id).one()
        job_id = job.id
    finally:
        db.close()

    thay = {}

    def gia_lap_sinh(db, doc, section=None, self_check=True, **kwargs):
        thay["model"] = llm.active_model()
        thay["self_check"] = self_check
        # Tạo Module thật chứ không trả về đối tượng giả: bước worker gán job.module_id nên
        # một id không có trong bảng modules sẽ vi phạm khoá ngoại.
        module = Module(document_id=doc.id, course_id=doc.course_id, title="M")
        db.add(module)
        db.flush()
        return module

    monkeypatch.setattr(pipeline, "run_module_generation", gia_lap_sinh)
    pipeline.run_module_generation_step(type("J", (), {"id": job_id})())

    assert thay["model"] == "groq-pro"
    assert thay["self_check"] is True


def test_tat_tu_kiem_luc_bam_sinh_di_vao_payload_va_toi_worker(monkeypatch):
    """Cờ của thí nghiệm có/không tự kiểm: ghi trong payload để biết module thuộc nhánh nào."""
    from app.generation import module_generation_pipeline as pipeline

    token, doc_id = _tai_lieu_cua_giang_vien()
    phan_hoi = client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"self_check": False, "output_language": "en"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert phan_hoi.status_code == 200, phan_hoi.text
    assert _payload_cua_job(doc_id)["self_check"] is False

    db = SessionLocal()
    try:
        job_id = db.query(Job).filter(Job.document_id == doc_id).one().id
    finally:
        db.close()
    thay = {}

    def gia_lap_sinh(db, doc, section=None, self_check=True, **kwargs):
        thay["self_check"] = self_check
        thay["output_language"] = kwargs["output_language"]
        module = Module(document_id=doc.id, course_id=doc.course_id, title="M")
        db.add(module)
        db.flush()
        return module

    monkeypatch.setattr(pipeline, "run_module_generation", gia_lap_sinh)
    pipeline.run_module_generation_step(type("J", (), {"id": job_id})())

    assert thay["self_check"] is False
    assert thay["output_language"] == "en"


def test_khong_noi_gi_thi_van_tu_kiem():
    token, doc_id = _tai_lieu_cua_giang_vien()

    client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert _payload_cua_job(doc_id)["self_check"] is True


def test_khong_chon_model_thi_sinh_duoc_ca_khi_gateway_dang_sap(monkeypatch):
    """Đường mặc định không được phụ thuộc vào việc gateway trả lời lúc bấm nút: chưa chọn
    model thì không có gì cần kiểm, nên không có lý do gì để gọi gateway."""
    import httpx

    def gateway_sap():
        raise httpx.ConnectError("gateway khong tra loi")

    monkeypatch.setattr(catalog, "fetch_model_info", gateway_sap)
    catalog.clear_cache()
    token, doc_id = _tai_lieu_cua_giang_vien()

    phan_hoi = client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert phan_hoi.status_code == 200, phan_hoi.text


def test_chon_model_ma_gateway_dang_sap_thi_bao_503(monkeypatch):
    import httpx

    def gateway_sap():
        raise httpx.ConnectError("gateway khong tra loi")

    monkeypatch.setattr(catalog, "fetch_model_info", gateway_sap)
    catalog.clear_cache()
    token, doc_id = _tai_lieu_cua_giang_vien()

    phan_hoi = client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "groq-pro"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert phan_hoi.status_code == 503
    assert _so_job(doc_id) == 0


def test_model_da_chon_duoc_thu_truoc_khi_xep_hang():
    token, doc_id = _tai_lieu_cua_giang_vien()

    client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "groq-pro"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert da_thu == ["groq-pro"]


def test_duong_mac_dinh_khong_thu_gi_ca():
    """Chưa chọn model thì không có gì để thử, và thêm một lệnh gọi vào mọi lượt bấm sinh
    chỉ tốn hạn mức vô ích."""
    token, doc_id = _tai_lieu_cua_giang_vien()

    client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert da_thu == []


def test_model_da_chon_khong_dung_duoc_thi_bao_422_va_khong_tao_job(monkeypatch):
    def model_hong(alias):
        raise catalog.ModelUnusable(alias, "chưa có API key hợp lệ cho nhà cung cấp của model này")

    monkeypatch.setattr(catalog, "probe", model_hong)
    token, doc_id = _tai_lieu_cua_giang_vien()

    phan_hoi = client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "groq-pro"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert phan_hoi.status_code == 422
    assert "groq-pro" in phan_hoi.json()["detail"]
    assert "api key" in phan_hoi.json()["detail"].lower()
    assert _so_job(doc_id) == 0


def test_mac_dinh_cua_giang_vien_khong_dung_duoc_cung_bi_chan(monkeypatch):
    """Mặc định lưu từ tuần trước có thể đã hết số dư hôm nay, nên cũng phải được thử."""
    def model_hong(alias):
        raise catalog.ModelUnusable(alias, "đã hết hạn mức hoặc đang bị giới hạn tốc độ, thử lại sau")

    monkeypatch.setattr(catalog, "probe", model_hong)
    token, doc_id = _tai_lieu_cua_giang_vien(mac_dinh="groq-pro")

    phan_hoi = client.post(f"/modules/generate?document_id={doc_id}", headers={"Authorization": f"Bearer {token}"})

    assert phan_hoi.status_code == 422
    assert _so_job(doc_id) == 0


def test_phep_thu_thay_gateway_sap_thi_bao_503(monkeypatch):
    def gateway_sap(alias):
        raise catalog.GatewayUnavailable("khong voi toi")

    monkeypatch.setattr(catalog, "probe", gateway_sap)
    token, doc_id = _tai_lieu_cua_giang_vien()

    phan_hoi = client.post(
        f"/modules/generate?document_id={doc_id}",
        json={"model_alias": "groq-pro"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert phan_hoi.status_code == 503
    assert _so_job(doc_id) == 0


@pytest.mark.parametrize("body,language", [({}, "vi"), ({"output_language": "en"}, "en"), ({"output_language": "vi"}, "vi")])
def test_output_language_is_saved_in_the_job(body, language):
    token, doc_id = _tai_lieu_cua_giang_vien()
    response = client.post(f"/modules/generate?document_id={doc_id}", json=body,
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert _payload_cua_job(doc_id)["output_language"] == language


def test_other_output_languages_are_rejected():
    token, doc_id = _tai_lieu_cua_giang_vien()
    response = client.post(f"/modules/generate?document_id={doc_id}", json={"output_language": "fr"},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert _so_job(doc_id) == 0
