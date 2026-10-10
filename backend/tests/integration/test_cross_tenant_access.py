"""One teacher must not be able to see or change anything that belongs to another.

Every endpoint that takes the id of a course, document, module, chat or job is attacked by a
second user with the first user's real ids. Each attempt must be refused with the same
answer as for an id that does not exist at all, so it does not even confirm that the id is
taken.

There are two layers, and each is tested on its own: the ownership checks in the endpoints
and row level security in the database. The attacks run once with both, and once with the
database layer taken away (a superuser session serves the requests), so an endpoint that
quietly relies on row level security alone is found. A last test fails when a route with a
resource id is added without being put here."""

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal, get_db
from app.main import app
from app.models import (
    ChatMessage,
    ChatRole,
    ChatSession,
    Chunk,
    ComponentType,
    Course,
    Document,
    DocumentSection,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    Study,
    User,
)

client = TestClient(app)


@pytest.fixture(scope="module")
def world():
    db = SessionLocal()
    try:
        owner = User(email=f"tenant-a-{uuid.uuid4().hex[:8]}@example.com", name="A", hashed_password="x", is_admin=False, moodle_user_id=10_000 + uuid.uuid4().int % 10**9)
        stranger = User(email=f"tenant-b-{uuid.uuid4().hex[:8]}@example.com", name="B", hashed_password="x", is_admin=False, moodle_user_id=10_000 + uuid.uuid4().int % 10**9)
        db.add_all([owner, stranger])
        db.flush()
        course = Course(name="Course of A", owner_id=owner.id)
        stranger_course = Course(name="Course of B", owner_id=stranger.id)
        db.add_all([course, stranger_course])
        db.flush()
        document = Document(course_id=course.id, filename="a.txt", status=DocumentStatus.embedded, raw_text="secret text of A")
        db.add(document)
        db.flush()
        db.add(Chunk(document_id=document.id, course_id=course.id, chunk_index=0, content="secret chunk of A"))
        db.add(DocumentSection(document_id=document.id, title="S", level=1, position=0, first_chunk_index=0, last_chunk_index=0))
        module = Module(document_id=document.id, course_id=course.id, title="Module of A")
        db.add(module)
        db.flush()
        component = ModuleComponent(module_id=module.id, type=ComponentType.content, content={"markdown": "secret A"})
        db.add(component)
        db.flush()
        quiz = ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={})
        db.add(quiz)
        db.flush()
        question = Question(component_id=quiz.id, stem="secret question A", choices={"options": ["A", "B"]}, answer_key="0")
        db.add(question)
        db.flush()
        session = ChatSession(course_id=course.id, title="chat of A")
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_id=session.id, role=ChatRole.user, content="secret chat of A", citations=[]))
        pipeline_job = Job(job_type=JobType.document_pipeline, document_id=document.id, step="extract", status=JobStatus.done)
        failed_job = Job(
            job_type=JobType.module_generation, document_id=document.id, step="generate",
            status=JobStatus.failed, last_error="secret error of A",
        )
        db.add_all([pipeline_job, failed_job])
        study = Study(name="Admin study", rubric_version="v1")
        db.add(study)
        db.commit()
        ids = {
            "owner": owner.id, "stranger": stranger.id, "course": course.id, "stranger_course": stranger_course.id,
            "document": document.id, "module": module.id, "component": component.id, "session": session.id,
            "pipeline_job": pipeline_job.id, "failed_job": failed_job.id, "question": question.id,
            "user": owner.id,
            "study": study.id,
        }
    finally:
        db.close()

    yield {
        **ids,
        "owner_headers": {"Authorization": f"Bearer {create_access_token(ids['owner'])}"},
        "stranger_headers": {"Authorization": f"Bearer {create_access_token(ids['stranger'])}"},
    }

    db = SessionLocal()
    try:
        db.query(Study).filter(Study.id == ids["study"]).delete(synchronize_session=False)
        db.query(Course).filter(Course.id.in_([ids["course"], ids["stranger_course"]])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["stranger"]])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(params=["rls-on", "rls-off"])
def layers(request):
    """rls-off serves the requests with a superuser session, to which row level security
    does not apply, so only the checks written in the endpoints stand between the users."""
    if request.param == "rls-off":
        def superuser_session():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = superuser_session
        yield request.param
        app.dependency_overrides.pop(get_db)
    else:
        yield request.param


# (method, path with named ids, query parameters, extra request arguments)
ATTACKS = [
    ("GET", "/documents", {"course_id": "{course}"}, {}),
    ("POST", "/documents", {"course_id": "{course}"}, {"files": {"file": ("x.txt", b"hi", "text/plain")}}),
    ("DELETE", "/documents/{document}", None, {}),
    ("GET", "/documents/{document}/status", None, {}),
    ("GET", "/documents/{document}/sections", None, {}),
    ("GET", "/documents/jobs/{pipeline_job}", None, {}),
    ("GET", "/modules", {"document_id": "{document}"}, {}),
    ("POST", "/modules/generate", {"document_id": "{document}"}, {}),
    ("GET", "/modules/jobs/{failed_job}", None, {}),
    ("GET", "/modules/{module}", None, {}),
    ("POST", "/modules/{module}/chat-edit", None, {"json": {"message": "shorten slide 1"}}),
    ("GET", "/modules/{module}/slides.pptx", None, {}),
    ("PATCH", "/modules/{module}", {"title": "pwned"}, {}),
    ("DELETE", "/modules/{module}", None, {}),
    ("PATCH", "/modules/{module}/components/{component}", None, {"json": {"content": {"markdown": "pwned"}}}),
    ("PATCH", "/modules/{module}/questions/{question}", None, {"json": {"stem": "pwned", "choices": ["A", "B"], "answer_key": 0, "explanation": "pwned"}}),
    ("POST", "/modules/{module}/publish-to-moodle", None, {"json": {"section_num": 0}}),
    ("POST", "/jobs/{failed_job}/retry", None, {}),
    # The admin's routes, reached by a lecturer: not found, as if there were nothing there.
    ("POST", "/admin/users/{user}/moodle-unlink", None, {}),
    ("POST", "/admin/users/{user}/disable", None, {}),
    ("POST", "/admin/users/{user}/enable", None, {}),
    ("POST", "/admin/users/{user}/password", None, {"json": {"password": "A pwned long phrase"}}),
    ("DELETE", "/admin/users/{user}", None, {}),
    ("GET", "/studies/{study}", None, {}),
    ("POST", "/studies/{study}/items", None, {"json": {"module_ids": ["{module}"]}}),
    ("DELETE", "/courses/{course}", None, {}),
    ("POST", "/courses/{course}/link-moodle", None, {"json": {"moodle_course_id": 999}}),
    ("DELETE", "/courses/{course}/link-moodle", None, {}),
    ("GET", "/courses/{course}/moodle-sections", None, {}),
    ("POST", "/courses/{course}/chat", None, {"json": {"message": "hello"}}),
    ("GET", "/courses/{course}/chat/{session}/history", None, {}),
    # A chat session of another course smuggled into one's own course.
    ("POST", "/courses/{stranger_course}/chat", None, {"json": {"message": "hello", "session_id": "{session}"}}),
    ("GET", "/courses/{stranger_course}/chat/{session}/history", None, {}),
]


def _fill(value, ids):
    if isinstance(value, str):
        for name, real in ids.items():
            value = value.replace("{" + name + "}", str(real))
        return value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


def _send(spec, ids, headers):
    method, path, query, extra = spec
    return client.request(method, _fill(path, ids), params=_fill(query, ids), headers=headers, **_fill(extra, ids))


OWNER_RESOURCES = (
    "course", "document", "module", "component", "session", "pipeline_job", "failed_job", "question", "user", "study"
)


def _ids_of_the_owner(world):
    # stranger_course is the attacker's own course: it exists, and is theirs.
    return {name: world[name] for name in (*OWNER_RESOURCES, "stranger_course")}


def _ids_that_do_not_exist(world):
    return {name: uuid.uuid4() for name in OWNER_RESOURCES} | {"stranger_course": world["stranger_course"]}


@pytest.mark.parametrize("spec", ATTACKS, ids=[f"{s[0]} {s[1]}" for s in ATTACKS])
def test_another_user_is_refused_with_the_same_answer_as_for_an_id_that_does_not_exist(world, layers, spec):
    against_real = _send(spec, _ids_of_the_owner(world), world["stranger_headers"])
    against_nothing = _send(spec, _ids_that_do_not_exist(world), world["stranger_headers"])

    assert against_real.status_code == 404
    assert (against_real.status_code, against_real.json()) == (against_nothing.status_code, against_nothing.json())


def test_nothing_of_the_owner_was_changed_by_the_attacks(world, layers):
    for spec in ATTACKS:
        _send(spec, _ids_of_the_owner(world), world["stranger_headers"])

    module = client.get(f"/modules/{world['module']}", headers=world["owner_headers"])
    documents = client.get("/documents", params={"course_id": world["course"]}, headers=world["owner_headers"])

    assert module.status_code == 200
    assert client.get("/courses", headers=world["owner_headers"]).status_code == 200  # still active
    assert module.json()["title"] == "Module of A"
    assert "secret A" in module.text
    assert [d["filename"] for d in documents.json()] == ["a.txt"]
    assert client.get("/courses", headers=world["owner_headers"]).json()[0]["id"] == str(world["course"])


def test_lists_only_ever_contain_the_callers_own_things(world, layers):
    courses = client.get("/courses", headers=world["stranger_headers"]).json()
    documents_of_a = client.get("/documents", params={"course_id": world["course"]}, headers=world["stranger_headers"])

    assert [c["id"] for c in courses] == [str(world["stranger_course"])]
    assert documents_of_a.status_code == 404


def test_a_lecturer_cannot_use_the_admin_pages(world, layers):
    for headers in (world["owner_headers"], world["stranger_headers"]):
        assert client.get("/admin/users", headers=headers).status_code == 404
        assert client.get("/admin/jobs/failed", headers=headers).status_code == 404
        assert client.post("/admin/jobs/retry", json={"job_ids": [str(world["failed_job"])]}, headers=headers).status_code == 404
        # The admin gives other people's lecturer accounts a lock and nothing else of theirs.
        assert client.post(f"/admin/users/{uuid.uuid4()}/disable", headers=headers).status_code == 404


PUBLIC = {"/auth/register", "/health", "/auth/login", "/auth/refresh", "/auth/logout"}
RESOURCE_QUERY = {"course_id", "document_id"}
BODY_METHODS = {"POST", "PATCH", "PUT"}


def _operations():
    """(method, path template, parameters) of every operation the application serves, read
    from its own API description so that a route added later is picked up automatically."""
    for path, item in app.openapi()["paths"].items():
        for method, operation in item.items():
            if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                yield method.upper(), path, operation.get("parameters", [])


def _normalise(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def test_no_route_answers_without_credentials_except_the_public_ones():
    checked, open_routes = 0, []
    for method, path, _parameters in _operations():
        if path in PUBLIC:
            continue
        url = re.sub(r"\{[^}]*\}", lambda _m: str(uuid.uuid4()), path)
        response = client.request(method, url, json={} if method in BODY_METHODS else None)
        checked += 1
        if response.status_code != 401:
            open_routes.append((method, path, response.status_code))

    assert checked > 20, "the sweep found almost no routes, so it proved nothing"
    assert open_routes == []


def test_every_route_that_takes_a_resource_id_is_attacked_above():
    covered = {(method, _normalise(path)) for method, path, _query, _extra in ATTACKS}
    needed = {
        (method, _normalise(path))
        for method, path, parameters in _operations()
        if path not in PUBLIC
        and any(p["in"] == "path" or (p["in"] == "query" and p["name"] in RESOURCE_QUERY) for p in parameters)
    }

    assert len(needed) > 15, "found almost no routes with a resource id, so this proved nothing"
    assert needed - covered == set(), "add these routes to ATTACKS so another user's access to them is tested"
