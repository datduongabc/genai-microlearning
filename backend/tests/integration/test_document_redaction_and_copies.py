"""What the extraction step does before anything stores a document's text: secrets and contact details are
replaced, a second copy of a file already in the course is refused, and a new file under an old name is noted."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.jobs.queue import enqueue_job, execute_job_step, handle_job_error
from app.main import app
from app.models import Course, Document, DocumentStatus, JobType, User
from tests.job_helpers import claimed

PAGE = """<html><body><h1>Lab 3</h1>
<p>Liên hệ trợ giảng: tro.giang@hcmut.edu.vn hoặc 0912 345 678.</p>
<p>Khoá thử: OPENROUTER_API_KEY=sk-or-v1-0123456789abcdef0123456789abcdef</p>
<p>Tiến trình chờ CPU nằm trong hàng đợi sẵn sàng.</p></body></html>"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    with SessionLocal() as db:
        user = User(email=f"redact-{uuid.uuid4().hex}@example.com", name="R", hashed_password="x")
        db.add(user)
        db.flush()
        course = Course(name="Redaction", owner_id=user.id)
        db.add(course)
        db.commit()
        ids = {"owner": user.id, "course": course.id, "dir": tmp_path}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["owner"]).delete(synchronize_session=False)
        db.commit()


def _extract(world, filename, content):
    key = f"{uuid.uuid4().hex}.html"
    (world["dir"] / key).write_text(content, encoding="utf-8")
    with SessionLocal() as db:
        doc = Document(course_id=world["course"], filename=filename, status=DocumentStatus.uploaded)
        db.add(doc)
        db.flush()
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id,
                          payload={"storage_key": key})
        db.commit()
        doc_id, job_id = doc.id, job.id
    with SessionLocal() as db:
        try:
            execute_job_step(claimed(db, job_id))
        except ValueError as error:
            handle_job_error(job_id, error)
    return doc_id


def _listed(world):
    response = TestClient(app).get("/documents", params={"course_id": world["course"]},
                                   headers={"Authorization": f"Bearer {create_access_token(world['owner'])}"})
    return {d["id"]: d for d in response.json()}


def test_secrets_and_contacts_never_reach_the_stored_text_or_the_next_step(world):
    doc_id = _extract(world, "lab3.html", PAGE)
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        stored = doc.raw_text + db.execute(
            text("select payload::text from jobs where document_id = :d"), {"d": doc_id}).scalar_one()
    for secret in ("tro.giang@hcmut.edu.vn", "0912 345 678", "sk-or-v1-0123456789abcdef"):
        assert secret not in stored
    assert "hàng đợi sẵn sàng" in stored
    assert _listed(world)[str(doc_id)]["extraction_warnings"] == [{"code": "redacted", "page": 0, "count": 3}]


def test_a_second_copy_of_a_file_is_refused_and_a_new_file_under_an_old_name_is_noted(world):
    first = _extract(world, "lab3.html", PAGE)
    copy = _extract(world, "lab3 (1).html", PAGE)
    newer = _extract(world, "lab3.html", PAGE.replace("Lab 3", "Lab 3, bản sửa"))
    listed = _listed(world)
    assert listed[str(copy)]["status"] == "failed"
    assert listed[str(copy)]["failures"][0]["error"] == (
        'Tệp này giống hệt tài liệu "lab3.html" đã có trong môn học. Hãy xoá một trong hai bản.')
    assert listed[str(first)]["status"] == "processing"
    assert {"code": "same_name", "page": 0} in listed[str(newer)]["extraction_warnings"]
