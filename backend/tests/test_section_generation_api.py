"""Generating modules from the sections a teacher picked, and listing them."""

import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.modules import MAX_SECTIONS_PER_REQUEST
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import (
    Course,
    Document,
    DocumentKind,
    DocumentSection,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    User,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    # The per-IP limit on /modules/generate guards against abuse; here dozens of
    # requests come from one test client within a minute.
    monkeypatch.setattr(limiter, "enabled", False)


def _make_user(db) -> User:
    user = User(
        email=f"api-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
    )
    db.add(user)
    db.flush()
    return user


def _add_document(db, course_id, kind, sections=0) -> tuple[Document, list[DocumentSection]]:
    doc = Document(
        course_id=course_id, filename=f"{kind.value}.pdf", status=DocumentStatus.embedded, kind=kind, raw_text="text"
    )
    db.add(doc)
    db.flush()
    rows = [
        DocumentSection(
            document_id=doc.id,
            title=f"Section {i}",
            level=1,
            position=i,
            first_chunk_index=i * 2,
            last_chunk_index=i * 2 + 1,
        )
        for i in range(sections)
    ]
    db.add_all(rows)
    db.flush()
    return doc, rows


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        owner, stranger = _make_user(db), _make_user(db)
        course = Course(name="Section API", owner_id=owner.id)
        db.add(course)
        db.flush()
        book, sections = _add_document(db, course.id, DocumentKind.textbook, sections=MAX_SECTIONS_PER_REQUEST + 2)
        _other_book, other_sections = _add_document(db, course.id, DocumentKind.textbook, sections=1)
        lecture, _ = _add_document(db, course.id, DocumentKind.lecture)
        no_outline, _ = _add_document(db, course.id, DocumentKind.textbook)
        db.commit()
        ids = {
            "owner": owner.id,
            "stranger": stranger.id,
            "course": course.id,
            "book": book.id,
            "sections": [s.id for s in sections],
            "foreign_section": other_sections[0].id,
            "lecture": lecture.id,
            "no_outline": no_outline.id,
        }
    finally:
        db.close()

    yield {
        **ids,
        "owner_headers": {"Authorization": f"Bearer {create_access_token(ids['owner'])}"},
        "stranger_headers": {"Authorization": f"Bearer {create_access_token(ids['stranger'])}"},
    }

    # Deleting the course cascades to documents, sections, modules and jobs.
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["stranger"]])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _generate(world, document_id, section_ids=None, headers=None):
    body = None if section_ids is None else {"section_ids": [str(s) for s in section_ids]}
    return client.post(
        "/modules/generate",
        params={"document_id": document_id},
        json=body,
        headers=headers or world["owner_headers"],
    )


def _jobs(document_id) -> list[Job]:
    db = SessionLocal()
    try:
        return db.query(Job).filter(Job.document_id == document_id, Job.job_type == JobType.module_generation).all()
    finally:
        db.close()


def _add_job(document_id, section_id, status) -> None:
    db = SessionLocal()
    try:
        db.add(
            Job(
                job_type=JobType.module_generation,
                step="generate",
                status=status,
                document_id=document_id,
                section_id=section_id,
            )
        )
        db.commit()
    finally:
        db.close()


def test_a_textbook_with_an_outline_requires_sections_to_be_picked(world):
    assert _generate(world, world["book"]).status_code == 422
    assert _generate(world, world["book"], []).status_code == 422
    assert _jobs(world["book"]) == []


def test_picking_sections_creates_one_job_per_section(world):
    picked = world["sections"][:2]

    response = _generate(world, world["book"], picked)

    assert response.status_code == 200
    assert [j["section_id"] for j in response.json()["jobs"]] == [str(s) for s in picked]
    jobs = _jobs(world["book"])
    assert {j.section_id for j in jobs} == set(picked)
    assert all(j.status == JobStatus.queued for j in jobs)


def test_more_sections_than_the_cap_are_refused_and_nothing_is_created(world):
    too_many = world["sections"][: MAX_SECTIONS_PER_REQUEST + 1]

    assert _generate(world, world["book"], too_many).status_code == 422
    assert _jobs(world["book"]) == []


def test_exactly_the_cap_is_accepted(world):
    response = _generate(world, world["book"], world["sections"][:MAX_SECTIONS_PER_REQUEST])

    assert response.status_code == 200
    assert len(response.json()["jobs"]) == MAX_SECTIONS_PER_REQUEST


def test_the_same_section_picked_twice_counts_once(world):
    section = world["sections"][0]

    response = _generate(world, world["book"], [section, section])

    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 1


def test_a_section_of_another_document_is_not_found(world):
    response = _generate(world, world["book"], [world["sections"][0], world["foreign_section"]])

    assert response.status_code == 404
    assert _jobs(world["book"]) == []


def test_a_section_already_generating_blocks_the_whole_request(world):
    _add_job(world["book"], world["sections"][0], JobStatus.running)

    response = _generate(world, world["book"], [world["sections"][0], world["sections"][1]])

    assert response.status_code == 409
    assert {j.section_id for j in _jobs(world["book"])} == {world["sections"][0]}


def test_a_finished_section_can_be_generated_again(world):
    _add_job(world["book"], world["sections"][0], JobStatus.done)

    response = _generate(world, world["book"], [world["sections"][0]])

    assert response.status_code == 200


def test_a_lecture_keeps_its_old_behaviour_and_refuses_sections(world):
    assert _generate(world, world["lecture"], [world["sections"][0]]).status_code == 422

    response = _generate(world, world["lecture"])

    assert response.status_code == 200
    assert set(response.json()) == {"job_id", "status"}
    assert [j.section_id for j in _jobs(world["lecture"])] == [None]


def test_a_lecture_with_a_generation_already_running_is_still_refused(world):
    _add_job(world["lecture"], None, JobStatus.running)

    assert _generate(world, world["lecture"]).status_code == 409


def test_a_textbook_without_an_outline_generates_from_the_whole_document(world):
    response = _generate(world, world["no_outline"])

    assert response.status_code == 200
    assert "job_id" in response.json()


def test_someone_elses_document_is_not_found(world):
    response = _generate(world, world["book"], [world["sections"][0]], headers=world["stranger_headers"])

    assert response.status_code == 404


def test_concurrent_requests_for_a_document_are_serialised_by_a_row_lock(world):
    """Two requests that both pass the 409 check before either enqueues would
    start the same section twice; the check must run under a lock on the
    document. Holding a lock on it from outside must therefore hold the request.

    NO KEY UPDATE, not UPDATE: the foreign key checks of the inserted jobs only
    need a key share, which the weaker lock lets through, so the request can
    only be held back by the lock the endpoint takes itself."""
    holder = SessionLocal()
    holder.execute(text("SELECT id FROM documents WHERE id = :id FOR NO KEY UPDATE"), {"id": world["book"]})
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(response=_generate(world, world["book"], [world["sections"][0]]))
    )
    try:
        thread.start()
        thread.join(timeout=1.5)
        held_back = thread.is_alive()
    finally:
        holder.rollback()
        holder.close()
    thread.join(timeout=15)

    assert held_back
    assert result["response"].status_code == 200


def test_the_sections_endpoint_lists_the_outline_with_per_section_state(world):
    db = SessionLocal()
    try:
        db.add(
            Module(
                document_id=world["book"],
                course_id=world["course"],
                title="Merge sort",
                section_id=world["sections"][0],
            )
        )
        db.commit()
    finally:
        db.close()
    _add_job(world["book"], world["sections"][1], JobStatus.running)

    response = client.get(f"/documents/{world['book']}/sections", headers=world["owner_headers"])

    assert response.status_code == 200
    rows = response.json()
    assert [r["id"] for r in rows] == [str(s) for s in world["sections"]]
    assert set(rows[0]) == {
        "id", "parent_id", "title", "level", "page_start", "page_end", "module_count", "generating",
    }
    assert (rows[0]["module_count"], rows[0]["generating"]) == (1, False)
    assert (rows[1]["module_count"], rows[1]["generating"]) == (0, True)


def test_the_sections_endpoint_is_private_to_the_owner(world):
    response = client.get(f"/documents/{world['book']}/sections", headers=world["stranger_headers"])

    assert response.status_code == 404


def test_the_document_list_reports_kind_and_section_count(world):
    response = client.get("/documents", params={"course_id": world["course"]}, headers=world["owner_headers"])

    by_id = {d["id"]: d for d in response.json()}
    assert by_id[str(world["book"])]["kind"] == "textbook"
    assert by_id[str(world["book"])]["section_count"] == MAX_SECTIONS_PER_REQUEST + 2
    assert by_id[str(world["lecture"])]["kind"] == "lecture"
    assert by_id[str(world["lecture"])]["section_count"] == 0


def test_a_section_job_makes_the_document_report_generating(world):
    _add_job(world["book"], world["sections"][0], JobStatus.queued)

    response = client.get("/documents", params={"course_id": world["course"]}, headers=world["owner_headers"])

    by_id = {d["id"]: d for d in response.json()}
    assert by_id[str(world["book"])]["generating"] is True
    assert by_id[str(world["lecture"])]["generating"] is False


def test_the_module_list_reports_the_section_of_each_module(world):
    db = SessionLocal()
    try:
        db.add(
            Module(
                document_id=world["book"], course_id=world["course"], title="A", section_id=world["sections"][0]
            )
        )
        db.add(Module(document_id=world["book"], course_id=world["course"], title="B"))
        db.commit()
    finally:
        db.close()

    response = client.get("/modules", params={"document_id": world["book"]}, headers=world["owner_headers"])

    assert {m["title"]: m["section_id"] for m in response.json()} == {"A": str(world["sections"][0]), "B": None}
