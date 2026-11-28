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
from datetime import UTC, datetime

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
    DocumentStatus,
    FlashcardSet,
    Job,
    JobStatus,
    JobType,
    MediaStatus,
    Module,
    ModuleComponent,
    Question,
    Study,
    StudyFile,
    StudyItem,
    User,
    Video,
)
from tests.integration.test_studies import human_packet

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
        flashcards = FlashcardSet(course_id=course.id, document_id=document.id, output_language="vi",
                                  model_alias="gemini-flash", cards=[], published_at=datetime.now(UTC))
        db.add(flashcards)
        db.flush()
        # Published and rendered, so that each attack on it is refused by the ownership check and not
        # by a missing file or a draft state that would refuse the owner too.
        video = Video(course_id=course.id, document_id=document.id, output_language="vi", model_alias="gemini-flash",
                      voice_alias="voice-heygen", image_alias="image-nano-banana", status=MediaStatus.ready,
                      scenes=[{"kind": "summary", "title": "secret scene of A", "bullets": ["a", "b"],
                               "narration": "secret narration of A", "citations": []}],
                      draft_version=1, rendered_version=1, video_key="videos/a/v1.mp4", published_at=datetime.now(UTC))
        db.add(video)
        db.flush()
        session = ChatSession(course_id=course.id, user_id=course.owner_id, title="chat of A")
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_id=session.id, role=ChatRole.user, content="secret chat of A", citations=[]))
        answer = ChatMessage(session_id=session.id, role=ChatRole.assistant, content="secret answer of A",
                             citations=[{"chunk_id": "c", "source_ref": "p1"}])
        db.add(answer)
        pipeline_job = Job(job_type=JobType.document_pipeline, document_id=document.id, step="extract", status=JobStatus.done)
        failed_job = Job(
            job_type=JobType.module_generation, document_id=document.id, step="generate",
            status=JobStatus.failed, last_error="secret error of A",
        )
        db.add_all([pipeline_job, failed_job])
        # The owner is an invited rater, so the study and its item are really there to be found.
        study = Study(name="Admin study", rubric_version="v1", rater_emails=[owner.email.lower()])
        db.add(study)
        db.flush()
        study_file = StudyFile(study_id=study.id, filename="reference.pdf", size=3, status="ready")
        db.add(study_file)
        db.flush()
        study_item = StudyItem(study_id=study.id, group="human", anon_code="M01", packet={**human_packet(), "anon_id": "M01"},
                               reference_file_id=study_file.id)
        db.add(study_item)
        db.flush()
        study.rater_pairs = {owner.email.lower(): [str(study_item.id)]}
        db.commit()
        ids = {
            "owner": owner.id, "stranger": stranger.id, "course": course.id, "stranger_course": stranger_course.id,
            "document": document.id, "module": module.id, "component": component.id, "session": session.id,
            "pipeline_job": pipeline_job.id, "failed_job": failed_job.id, "question": question.id,
            "user": owner.id, "flashcard_set": flashcards.id, "video": video.id, "answer": answer.id,
            "study": study.id, "study_item": study_item.id, "study_file": study_file.id,
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
_SCORE = {"score": 1, "justification": "pwned"}
RATING = {
    "anon_id": "M01", "overall_notes": "pwned", "hallucination_findings": [],
    **{name: _SCORE for name in (
        "accuracy", "completeness", "consistency", "hallucination_score", "slide_quality", "quiz_quality",
        "bloom_fidelity", "query_alignment",
    )},
}

VIDEO_SCENE = {"kind": "summary", "title": "pwned", "bullets": [], "narration": "pwned"}
VIDEO_EDIT = {"draft_version": 1, "scenes": [VIDEO_SCENE]}

ATTACKS = [
    ("GET", "/documents", {"course_id": "{course}"}, {}),
    ("POST", "/documents/uploads", {"course_id": "{course}"}, {"json": {"filename": "x.html", "size": 2}}),
    ("PUT", "/documents/{document}/content", None, {"content": b"hi"}),
    ("POST", "/documents/{document}/complete", None, {}),
    ("DELETE", "/documents/{document}", None, {}),
    ("GET", "/documents/{document}/status", None, {}),
    ("GET", "/documents/{document}/sections", None, {}),
    ("GET", "/documents/{document}/chunks", None, {}),
    ("GET", "/documents/jobs/{pipeline_job}", None, {}),
    ("GET", "/modules", {"document_id": "{document}"}, {}),
    ("GET", "/modules/concepts", {"document_id": "{document}"}, {}),
    ("POST", "/modules/generate", {"document_id": "{document}"}, {}),
    ("GET", "/modules/jobs/{failed_job}", None, {}),
    ("GET", "/modules/{module}", None, {}),
    ("POST", "/modules/{module}/chat-edit", None, {"json": {"message": "shorten slide 1"}}),
    ("GET", "/modules/{module}/slides.pptx", None, {}),
    ("GET", "/modules/{module}/scorm.zip", None, {}),
    ("PATCH", "/modules/{module}", {"title": "pwned"}, {}),
    ("DELETE", "/modules/{module}", None, {}),
    ("PATCH", "/modules/{module}/components/{component}", None, {"json": {"content": {"markdown": "pwned"}, "version": 1}}),
    ("PATCH", "/modules/{module}/questions/{question}", None, {"json": {"stem": "pwned", "choices": ["A", "B"], "answer_key": 0, "explanation": "pwned", "version": 1}}),
    ("POST", "/modules/{module}/publish-to-moodle", None, {"json": {"section_num": 0}}),
    ("POST", "/flashcard-sets", {"document_id": "{document}"}, {"json": {"output_language": "vi"}}),
    ("GET", "/courses/{course}/flashcard-sets", None, {}),
    ("GET", "/flashcard-sets/{flashcard_set}", None, {}),
    ("PUT", "/flashcard-sets/{flashcard_set}/cards", None, {"json": {"draft_version": 1, "cards": []}}),
    ("POST", "/flashcard-sets/{flashcard_set}/revise", None, {"json": {"draft_version": 1, "message": "x"}}),
    ("POST", "/flashcard-sets/{flashcard_set}/publish", None, {}),
    ("POST", "/flashcard-sets/{flashcard_set}/unpublish", None, {}),
    ("DELETE", "/flashcard-sets/{flashcard_set}", None, {}),
    ("POST", "/videos", {"document_id": "{document}"}, {"json": {"output_language": "vi"}}),
    ("GET", "/courses/{course}/videos", None, {}),
    ("GET", "/videos/{video}", None, {}),
    ("PUT", "/videos/{video}/scenes", None, {"json": VIDEO_EDIT}),
    ("POST", "/videos/{video}/illustrations", None, {"json": {}}),
    ("POST", "/videos/{video}/render", None, {}),
    ("POST", "/videos/{video}/revise", None, {"json": {"draft_version": 1, "message": "x"}}),
    ("POST", "/videos/{video}/publish", None, {}),
    ("POST", "/videos/{video}/unpublish", None, {}),
    ("GET", "/videos/{video}/file", None, {}),
    ("GET", "/videos/{video}/file/content", None, {}),
    ("GET", "/videos/{video}/scenes/{scene_index}/preview.png", None, {}),
    ("DELETE", "/videos/{video}", None, {}),
    ("GET", "/learn/courses/{course}/items", None, {}),
    ("GET", "/learn/flashcard-sets/{flashcard_set}", None, {}),
    ("GET", "/learn/videos/{video}", None, {}),
    ("GET", "/learn/videos/{video}/file/content", None, {}),
    ("POST", "/jobs/{failed_job}/retry", None, {}),
    # The admin's routes, reached by a lecturer: not found, as if there were nothing there.
    ("POST", "/admin/users/{user}/moodle-unlink", None, {}),
    ("POST", "/admin/users/{user}/disable", None, {}),
    ("POST", "/admin/users/{user}/enable", None, {}),
    ("POST", "/admin/users/{user}/password", None, {"json": {"password": "A pwned long phrase"}}),
    ("POST", "/admin/users/{user}/erase", None, {}),
    ("GET", "/admin/users/{user}/courses", None, {}),
    ("POST", "/admin/courses/{course}/transfer", None, {"json": {"new_owner_id": "{user}"}}),
    ("DELETE", "/admin/users/{user}", None, {}),
    ("GET", "/studies/{study}", None, {}),
    ("POST", "/studies/{study}/items", None, {"json": {"module_ids": ["{module}"]}}),
    ("GET", "/studies/{study}/raters", None, {}),
    ("GET", "/studies/{study}/candidates", None, {}),
    ("POST", "/studies/{study}/pairs", None, {"json": {"module_id": "{module}"}}),
    ("GET", "/studies/{study}/media-candidates", None, {}),
    ("POST", "/studies/{study}/media", None, {"json": {"kind": "video", "id": "{module}"}}),
    ("GET", "/studies/mine/{study}/items/{study_item}/video", None, {}),
    ("GET", "/studies/mine/{study}/items/{study_item}/video/content", None, {}),
    ("PUT", "/studies/{study}/raters", None, {"json": {"emails": []}}),
    ("DELETE", "/studies/{study}/items/{study_item}", None, {}),
    ("PUT", "/studies/{study}/items/{study_item}/topic", None, {"json": {"topic": "x"}}),
    ("GET", "/studies/{study}/results/media.csv", None, {}),
    # A study the owner was invited to rate, reached by a lecturer who was not.
    ("GET", "/studies/mine/{study}/items", None, {}),
    ("PUT", "/studies/mine/{study}/items/{study_item}/rating", None, {"json": RATING}),
    ("POST", "/studies/{study}/judge", None, {"json": {}}),
    ("GET", "/studies/{study}/judge/{failed_job}", None, {}),
    ("GET", "/studies/{study}/results", None, {}),
    ("GET", "/studies/{study}/results/raters/{rater_number}.csv", None, {}),
    ("GET", "/studies/{study}/results/judge.jsonl", None, {}),
    ("GET", "/studies/{study}/files", None, {}),
    ("POST", "/studies/{study}/files", None, {"json": {"filename": "x.pdf", "size": 2}}),
    ("PUT", "/studies/{study}/files/{study_file}/content", None, {"content": b"hi"}),
    ("POST", "/studies/{study}/files/{study_file}/complete", None, {}),
    ("DELETE", "/studies/{study}/files/{study_file}", None, {}),
    ("GET", "/studies/{study}/files/{study_file}/download", None, {}),
    ("GET", "/studies/{study}/files/{study_file}/content", None, {}),
    ("PUT", "/studies/{study}/items/{study_item}/reference", None, {"json": {"file_id": None}}),
    # The reference file of a packet the owner was invited to rate.
    ("GET", "/studies/mine/{study}/items/{study_item}/reference", None, {}),
    ("GET", "/studies/mine/{study}/items/{study_item}/reference/content", None, {}),
    ("DELETE", "/courses/{course}", None, {}),
    ("POST", "/courses/{course}/link-moodle", None, {"json": {"moodle_course_id": 999}}),
    ("DELETE", "/courses/{course}/link-moodle", None, {}),
    ("GET", "/courses/{course}/moodle-sections", None, {}),
    ("POST", "/courses/{course}/chat/stream", None, {"json": {"message": "hello"}}),
    ("GET", "/courses/{course}/chat/{session}/history", None, {}),
    ("GET", "/courses/{course}/chat/latest", None, {}),
    ("POST", "/courses/{course}/chat/sessions", None, {}),
    ("GET", "/courses/{course}/chat/feedback", None, {}),
    ("PUT", "/courses/{course}/chat/messages/{answer}/feedback", None, {"json": {"feedback": "not_right"}}),
    ("POST", "/courses/{course}/chat/messages/{answer}/opened", None, {"json": {"citation": 1}}),
    ("PATCH", "/documents/{document}", None, {"json": {"learner_access": True}}),
    ("PATCH", "/courses/{course}", None, {"json": {"name": "Đổi"}}),
    # A chat session of another course smuggled into one's own course.
    ("POST", "/courses/{stranger_course}/chat/stream", None, {"json": {"message": "hello", "session_id": "{session}"}}),
    ("GET", "/courses/{stranger_course}/chat/{session}/history", None, {}),
    ("PUT", "/courses/{stranger_course}/chat/messages/{answer}/feedback", None, {"json": {"feedback": "not_right"}}),
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
    "course", "document", "module", "component", "session", "pipeline_job", "failed_job", "question", "user", "study",
    "study_item", "study_file", "flashcard_set", "video", "answer",
)


def _ids_of_the_owner(world):
    # stranger_course is the attacker's own course: it exists, and is theirs.
    # A rater number is no resource of anyone: the same one is asked either way.
    return {name: world[name] for name in (*OWNER_RESOURCES, "stranger_course")} | {"rater_number": 1, "scene_index": 0}


def _ids_that_do_not_exist(world):
    return {name: uuid.uuid4() for name in OWNER_RESOURCES} | {"stranger_course": world["stranger_course"], "rater_number": 1,
                                                             "scene_index": 0}


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
    assert [(d["filename"], d["learner_access"]) for d in documents.json()] == [("a.txt", False)]
    video = client.get(f"/videos/{world['video']}", headers=world["owner_headers"]).json()
    assert (video["scenes"][0]["title"], video["draft_version"]) == ("secret scene of A", 1)
    assert video["published_at"] is not None and video["rendered_version"] == 1
    # The invited owner still finds the study that the stranger was told is not there, unrated.
    study = client.get(f"/studies/mine/{world['study']}/items", headers=world["owner_headers"]).json()
    assert [(item["id"], item["rating"]) for item in study["items"]] == [(str(world["study_item"]), None)]
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


# /ready is called by the load balancer, which has no login. It says only whether the database answers.
# The LTI routes come before any sign-in: the LMS's signature, the one-time state and the
# one-time launch code protect them instead (tests/integration/test_lti_launch.py).
# /auth/options is read by the sign-in page before anyone signs in; it says only whether
# self registration is open.
PUBLIC = {"/auth/register", "/auth/options", "/health", "/ready", "/auth/login", "/auth/refresh", "/auth/logout",
          "/lti/login", "/lti/launch", "/lti/session", "/lti/jwks"}
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
