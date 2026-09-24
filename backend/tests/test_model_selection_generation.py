"""Model giảng viên chọn đi được từ request tới lượt sinh chạy trong worker.

Request và worker là hai tiến trình khác nhau, request đã trả về từ lâu trước khi worker
bắt đầu, nên alias phải nằm trong payload của job chứ không thể giữ trong bộ nhớ.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
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
        "litellm_params": {"model": "gemini/gemini-flash-lite-latest"},
        "model_info": {"mode": "chat"},
    },
    {
        "model_name": "groq-pro",
        "litellm_params": {"model": "groq/openai/gpt-oss-120b"},
        "model_info": {"mode": "chat"},
    },
]


@pytest.fixture(autouse=True)
def moi_truong(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(catalog, "fetch_model_info", lambda: GATEWAY_GIA_LAP)
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

    def gia_lap_sinh(db, doc, section=None):
        thay["model"] = llm._active_model()
        # Tạo Module thật chứ không trả về đối tượng giả: bước worker gán job.module_id nên
        # một id không có trong bảng modules sẽ vi phạm khoá ngoại.
        module = Module(document_id=doc.id, course_id=doc.course_id, title="M")
        db.add(module)
        db.flush()
        return module

    monkeypatch.setattr(pipeline, "run_module_generation", gia_lap_sinh)
    pipeline.run_module_generation_step(type("J", (), {"id": job_id})())

    assert thay["model"] == "groq-pro"


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
