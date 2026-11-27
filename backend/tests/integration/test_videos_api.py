"""The lecturer's routes for videos: generate, edit the scenes, draw, render, publish, delete, preview."""
import io
import json
import threading
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import videos as videos_api
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.generation.video_script import MAX_ILLUSTRATIONS, Scene
from app.llm import catalog
from app.main import app
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    MediaStatus,
    User,
    Video,
)
from app.storage import S3UploadStore, media_store
from tests.auth_helpers import admin_headers

client = TestClient(app)


def headers(user_id):
    return {"Authorization": "Bearer " + create_access_token(user_id)}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(catalog, "is_available", lambda alias: alias in {"gemini-flash", "claude-fast"})
    monkeypatch.setattr(catalog, "probe", lambda alias: None)
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))
    with SessionLocal() as db:
        def user(name, **extra):
            u = User(email=f"vid-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name, hashed_password="x", **extra)
            db.add(u)
            db.flush()
            return u
        owner, other, learner = user("owner"), user("other"), user("learner", can_author=False)
        course = Course(name="Videos", owner_id=owner.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="Chapter 5.pdf", status=DocumentStatus.embedded,
                       raw_text="FCFS runs jobs in order.")
        other_doc = Document(course_id=course.id, filename="y.txt", status=DocumentStatus.embedded, raw_text="Graphs.")
        db.add_all([doc, other_doc])
        db.flush()
        chunk = Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="FCFS runs jobs in order.",
                      source_ref=json.dumps({"page": 9}))
        chunk2 = Chunk(document_id=doc.id, course_id=course.id, chunk_index=1, content="Round Robin uses a quantum.",
                       source_ref=json.dumps({"page": 10}))
        foreign_chunk = Chunk(document_id=other_doc.id, course_id=course.id, chunk_index=0, content="A graph.")
        db.add_all([chunk, chunk2, foreign_chunk])
        db.commit()
        ids = {
            "owner": owner.id, "other": other.id, "learner": learner.id, "course": course.id, "doc": doc.id,
            "chunk": str(chunk.id), "chunk2": str(chunk2.id), "foreign_chunk": str(foreign_chunk.id),
        }
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete()
        db.query(User).filter(User.id.in_([ids["owner"], ids["other"], ids["learner"]])).delete()
        db.commit()


def scene(world, **extra) -> dict:
    """A stored scene as the script job writes it: two cited bullets and two cited sentences."""
    c = world["chunk"]
    fields = {
        "kind": "summary", "title": "FCFS", "bullets": ["Đến trước", "Phục vụ trước"],
        "bullet_citations": [[c], [c]], "narration": "FCFS chạy theo thứ tự đến.\nTiến trình đến trước chạy trước.",
        "narration_citations": [[c], [c]], "citations": [c],
    }
    return Scene(**(fields | extra)).model_dump()


def edit(stored: dict, **changes) -> dict:
    """The scene as the editor sends it back: what it was given, with the lecturer's changes."""
    fields = ("kind", "title", "bullets", "bullet_citations", "formula", "code", "table", "diagram", "image_prompt",
              "narration", "narration_citations")
    return {f: stored.get(f) for f in fields} | changes


def make_video(world, scenes=None, *, status=MediaStatus.draft, published=False, **extra):
    with SessionLocal() as db:
        video = Video(
            course_id=world["course"], document_id=world["doc"], focus="FCFS", output_language="vi",
            model_alias="gemini-flash", voice_alias="voice-heygen", image_alias="image-nano-banana",
            scenes=scenes if scenes is not None else [scene(world)], status=status,
            published_at=datetime.now(UTC) if published else None, **extra)
        db.add(video)
        db.commit()
        return video.id


def add_job(world, video_id, job_type, status=JobStatus.running):
    with SessionLocal() as db:
        job = Job(job_type=job_type, step="x", document_id=world["doc"], status=status,
                  payload={"video_id": str(video_id)})
        db.add(job)
        db.commit()
        return job.id


def finish_jobs(video_id):
    with SessionLocal() as db:
        for job in db.query(Job).filter(Job.payload["video_id"].astext == str(video_id)):
            job.status = JobStatus.done
        db.commit()


def stored_video(video_id) -> Video:
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        db.expunge(video)
        return video


def store_file(key: str, data: bytes = b"x") -> None:
    path = media_store().path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def png(size=(400, 300), color="steelblue") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


# --- Generating ---

def test_generating_reserves_one_generation(world):
    response = client.post("/videos", params={"document_id": str(world["doc"])}, headers=headers(world["owner"]), json={
        "focus": "FCFS", "output_language": "vi", "model_alias": "gemini-flash",
        "voice_alias": "voice-gemini-lite", "image_alias": "image-qwen"})

    assert response.status_code == 200
    with SessionLocal() as db:
        video = db.get(Video, uuid.UUID(response.json()["id"]))
        job = db.get(Job, uuid.UUID(response.json()["job_id"]))
        assert (video.status, video.focus, video.model_alias, video.voice_alias, video.image_alias) == (
            MediaStatus.generating, "FCFS", "gemini-flash", "voice-gemini-lite", "image-qwen")
        assert (job.job_type, job.document_id, job.payload) == (
            JobType.video_script, world["doc"], {"video_id": str(video.id)})
        assert db.get(User, world["owner"]).generation_count == 1


def test_the_daily_generation_limit_names_videos_not_modules(world, monkeypatch):
    monkeypatch.setattr(settings, "non_teacher_daily_generation_limit", 1)
    params, h = {"document_id": str(world["doc"])}, headers(world["owner"])
    assert client.post("/videos", params=params, json={"output_language": "vi", "focus": "a"}, headers=h).status_code == 200

    over = client.post("/videos", params=params, json={"output_language": "vi", "focus": "b"}, headers=h)

    assert over.status_code == 429
    assert "tối đa 1 video" in over.json()["detail"] and "mô-đun" not in over.json()["detail"]


def test_the_default_voice_and_image_model_are_used_when_none_is_asked(world):
    response = client.post("/videos", params={"document_id": str(world["doc"])}, json={"output_language": "en"},
                           headers=headers(world["owner"]))

    video = stored_video(uuid.UUID(response.json()["id"]))
    assert (video.voice_alias, video.image_alias, video.output_language) == ("voice-heygen", "image-nano-banana", "en")


def test_an_identical_request_while_one_is_running_is_refused_without_a_charge(world):
    params, body, h = {"document_id": str(world["doc"])}, {"output_language": "vi", "focus": "FCFS"}, headers(world["owner"])
    assert client.post("/videos", params=params, json=body, headers=h).status_code == 200
    assert client.post("/videos", params=params, json=body, headers=h).status_code == 409
    assert client.post("/videos", params=params, json=body | {"focus": "RR"}, headers=h).status_code == 200
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).generation_count == 2


@pytest.mark.parametrize("body", [
    {"output_language": "vi", "voice_alias": "voice-nobody"},
    {"output_language": "vi", "image_alias": "image-nobody"},
    {"output_language": "vi", "model_alias": "no-such-model"},
    {"output_language": "fr"},
])
def test_an_unusable_request_is_rejected_with_422_and_costs_nothing(world, body):
    response = client.post("/videos", params={"document_id": str(world["doc"])}, json=body,
                           headers=headers(world["owner"]))

    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).generation_count == 0
        assert db.query(Video).filter(Video.course_id == world["course"]).count() == 0


# --- Reading ---

def test_the_detail_shows_what_the_editor_needs(world):
    c, c2 = world["chunk"], world["chunk2"]
    scenes = [
        scene(world, kind="illustration", image_prompt="a queue of P1 P2", image_key="videos/a/img-0.jpg",
              image_prompt_used="an older queue", citations=[c, c2], narration_citations=[[c], [c2]]),
        scene(world, kind="illustration", image_prompt="a clock"),
        scene(world, narration="x" * 300),
    ]
    video_id = make_video(world, scenes, draft_version=3, rendered_version=2, video_key="videos/a/v2.mp4",
                          error="Không vẽ được hình cho cảnh 2")
    add_job(world, video_id, JobType.video_images, JobStatus.queued)

    body = client.get(f"/videos/{video_id}", headers=headers(world["owner"])).json()

    assert (body["course_id"], body["document_id"]) == (str(world["course"]), str(world["doc"]))
    assert (body["draft_version"], body["rendered_version"], body["error"]) == (3, 2, "Không vẽ được hình cho cảnh 2")
    assert body["job"]["job_type"] == "video_images" and body["job"]["status"] == "queued"
    first, second, third = body["scenes"]
    assert (first["has_image"], first["image_outdated"], first["needs_image"]) == (True, True, False)
    assert (second["has_image"], second["image_outdated"], second["needs_image"]) == (False, False, True)
    assert (third["image_outdated"], third["needs_image"]) == (False, False)
    assert "image_key" not in first and "voice_key" not in first
    assert first["sources"] == [
        {"chunk_id": c, "locator": "Chapter 5.pdf · trang 9", "content": "FCFS runs jobs in order.",
         "snippet": "FCFS runs jobs in order."},
        {"chunk_id": c2, "locator": "Chapter 5.pdf · trang 10", "content": "Round Robin uses a quantum.",
         "snippet": "Round Robin uses a quantum."},
    ]
    assert body["render_problems"] == ["Cảnh 2: chưa vẽ hình minh họa"]
    # An outdated picture is a warning, never a reason to refuse the render.
    assert body["render_warnings"] == ["Cảnh 1: hình được vẽ theo mô tả cũ; bấm Vẽ lại nếu cần"]
    narration_chars = sum(len(s["narration"]) for s in scenes)
    assert body["estimated_seconds"] == pytest.approx(narration_chars / 15, abs=0.1)

    listed = client.get(f"/courses/{world['course']}/videos", headers=headers(world["owner"])).json()
    assert [(v["id"], v["course_id"], v["scene_count"], v["rendered_version"]) for v in listed] == [
        (str(video_id), str(world["course"]), 3, 2)]


# --- Editing ---

def test_editing_a_published_video_keeps_the_rendered_one_for_learners(world):
    stored = scene(world, kind="illustration", image_prompt="a queue", image_key="videos/a/img-0.jpg",
                   image_prompt_used="a queue", voice_key="videos/a/voice-0.wav",
                   voice_text_used="FCFS chạy theo thứ tự đến. Tiến trình đến trước chạy trước.",
                   voice_alias_used="voice-heygen")
    video_id = make_video(world, [stored], status=MediaStatus.ready, published=True, draft_version=1,
                          rendered_version=1, video_key="videos/a/v1.mp4", duration_seconds=12.5)
    before = stored_video(video_id)

    response = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={"draft_version": 1, "scenes": [
        edit(stored, narration="FCFS chạy theo thứ tự đến.\nCâu mới của giảng viên.",
             narration_citations=[[world["chunk"]], [world["chunk"]]])]})

    assert response.status_code == 200
    after = stored_video(video_id)
    assert (after.rendered_version, after.video_key, after.published_at, after.duration_seconds) == (
        1, "videos/a/v1.mp4", before.published_at, 12.5)
    assert after.draft_version == 2
    edited = after.scenes[0]
    assert (edited["image_key"], edited["image_prompt_used"], edited["voice_key"], edited["voice_text_used"]) == (
        stored["image_key"], stored["image_prompt_used"], stored["voice_key"], stored["voice_text_used"])
    assert edited["author"] == "lecturer"
    assert response.json()["draft_version"] == 2 and response.json()["rendered_version"] == 1


def test_an_edit_keeps_the_flags_of_unchanged_items_and_none_on_the_lecturers_uncited_ones(world):
    c = world["chunk"]
    short = "Lời thoại chỉ có 2 cảnh (nên có 8 đến 10) vì model chỉ viết từng ấy cảnh: hãy thêm cảnh hoặc sinh lại"
    flagged = scene(world, flags=["Ý 1: ngược nghĩa", "Ý 2: không có trong nguồn", "Câu 2: sai số", short])
    untouched = scene(world, title="Round Robin", flags=["Câu 1: nghi sai"])
    video_id = make_video(world, [flagged, untouched])

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={"draft_version": 1, "scenes": [
        edit(flagged, bullets=["Đến trước được phục vụ", "Phục vụ trước"],
             narration="FCFS chạy theo thứ tự đến.\nGiảng viên viết lại câu này.",
             narration_citations=[[c], []]),
        edit(untouched),
        {"kind": "title", "title": "Cảnh mới", "bullets": ["Một ý"], "bullet_citations": [[]],
         "narration": "Câu mới.", "narration_citations": [[c]]},
    ]}).json()

    first, second, third = body["scenes"]
    assert first["author"] == "lecturer"
    # The lecturer cannot cite a passage, so a scene they changed has no "Chưa có nguồn".
    assert first["flags"] == ["Ý 2: không có trong nguồn", short]
    assert (second["author"], second["flags"]) == ("model", ["Câu 1: nghi sai"])
    assert (third["author"], third["flags"]) == ("lecturer", [])
    assert third["citations"] == [c]
    assert first["narration_citations"] == [[c], []]


def test_citing_a_passage_clears_the_no_source_flag_and_a_flag_can_be_dismissed(world):
    c = world["chunk"]
    stored = scene(world, author="lecturer", narration_citations=[[c], []],
                   flags=["Chưa có nguồn: Câu 2", "Ý 1: ngược nghĩa"])
    video_id = make_video(world, [stored])

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={"draft_version": 1, "scenes": [
        edit(stored, narration_citations=[[c], [world["chunk2"]]], flags=[])]}).json()

    assert body["scenes"][0]["flags"] == []
    assert body["scenes"][0]["citations"] == [c, world["chunk2"]]


def test_an_uncited_sentence_keeps_its_no_source_flag_whatever_the_editor_sends(world):
    c = world["chunk"]
    # Stored without its flag (an older save) and sent back unchanged with every flag dismissed.
    stored = scene(world, author="model", narration_citations=[[c], []], flags=["Câu 1: nghi sai"])
    video_id = make_video(world, [stored])

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={
        "draft_version": 1, "scenes": [edit(stored, flags=[])]}).json()

    assert body["scenes"][0]["flags"] == ["Chưa có nguồn: Câu 2"]


def test_an_uncited_sentence_of_the_model_shows_its_flag_before_the_first_save(world):
    by_model = scene(world, author="model", narration_citations=[[world["chunk"]], []], flags=[])
    by_lecturer = scene(world, author="lecturer", narration_citations=[[world["chunk"]], []], flags=[])
    video_id = make_video(world, [by_model, by_lecturer])

    shown = client.get(f"/videos/{video_id}", headers=headers(world["owner"])).json()["scenes"]

    assert [s["flags"] for s in shown] == [["Chưa có nguồn: Câu 2"], []]
    assert stored_video(video_id).scenes[0]["flags"] == []


def test_a_flag_follows_its_item_when_the_items_before_it_are_removed(world):
    c, c2 = world["chunk"], world["chunk2"]
    stored = scene(world, bullets=["Một", "Hai", "Ba"], bullet_citations=[[c], [c], [c2]],
                   narration="Câu A.\nCâu B.", narration_citations=[[c], [c2]],
                   flags=["Ý 1: sai", "Ý 3: không có trong nguồn", "Câu 2: nghi sai"])
    video_id = make_video(world, [stored])

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={
        "draft_version": 1, "scenes": [edit(stored, bullets=["Hai", "Ba"], bullet_citations=[[c], [c2]],
                                            narration="Câu B.\nCâu A.", narration_citations=[[c2], [c]])]}).json()

    assert body["scenes"][0]["flags"] == ["Ý 2: không có trong nguồn", "Câu 1: nghi sai"]


def test_a_save_without_the_draft_it_was_made_from_is_422(world):
    video_id = make_video(world)

    response = client.put(f"/videos/{video_id}/scenes", json={"scenes": [edit(scene(world))]},
                          headers=headers(world["owner"]))

    assert response.status_code == 422
    assert stored_video(video_id).draft_version == 1


def test_scenes_keep_their_pictures_and_flags_when_moved_or_removed(world):
    first = scene(world, kind="illustration", title="A", image_prompt="a queue", image_key="videos/v/img-0-a.jpg",
                  image_prompt_used="a queue", voice_key="videos/v/voice-0-a.wav", flags=["Ý 1: nghi sai"])
    second = scene(world, kind="illustration", title="B", image_prompt="a clock", image_key="videos/v/img-1-b.jpg",
                   image_prompt_used="a clock", flags=["Câu 1: nghi sai"])
    video_id = make_video(world, [first, second])
    for key in ("videos/v/img-0-a.jpg", "videos/v/voice-0-a.wav", "videos/v/img-1-b.jpg"):
        store_file(key)

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={"draft_version": 1, "scenes": [
        edit(second, from_index=1), edit(first, from_index=None, title="A, viết lại")]}).json()

    moved, rewritten = stored_video(video_id).scenes
    assert (moved["image_key"], moved["author"], moved["flags"]) == ("videos/v/img-1-b.jpg", "model", ["Câu 1: nghi sai"])
    assert (rewritten["image_key"], rewritten["voice_key"], rewritten["author"]) == (None, None, "lecturer")
    assert body["scenes"][1]["needs_image"] is True
    # The scene that is gone takes its files with it; the one still shown keeps its picture.
    assert media_store().size("videos/v/img-0-a.jpg") is None
    assert media_store().size("videos/v/voice-0-a.wav") is None
    assert media_store().size("videos/v/img-1-b.jpg") is not None


def test_a_save_reads_the_video_locked_so_a_picture_committed_meanwhile_is_not_overwritten(world):
    """The save carries image_key from the stored scenes: read without a lock, a drawing job could
    commit a new key between that read and the save's commit, and the save would put back the old
    one. Locked, the job's own locked read waits for the save, then sees the edit."""
    from sqlalchemy import event

    from app.db import engine, request_engine
    video_id = make_video(world)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)
    for bound in (engine, request_engine):
        event.listen(bound, "before_cursor_execute", capture)
    try:
        response = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]),
                              json={"draft_version": 1, "scenes": [edit(scene(world), from_index=0)]})
    finally:
        for bound in (engine, request_engine):
            event.remove(bound, "before_cursor_execute", capture)

    assert response.status_code == 200
    reads = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "FROM videos" in s]
    assert reads and "FOR UPDATE" in reads[0]


def test_files_of_a_removed_scene_stay_while_a_job_may_still_read_them(world):
    first = scene(world, voice_key="videos/v/voice-0-a.wav")
    video_id = make_video(world, [first, scene(world)])
    store_file("videos/v/voice-0-a.wav")
    add_job(world, video_id, JobType.video_render)

    response = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]),
                          json={"draft_version": 1, "scenes": [edit(scene(world), from_index=1)]})

    assert response.status_code == 200
    assert media_store().size("videos/v/voice-0-a.wav") is not None


def test_emptying_the_description_drops_the_picture_and_its_file(world):
    drawn = scene(world, kind="illustration", image_prompt="a queue", image_key="videos/v/img-0-a.jpg",
                  image_prompt_used="a queue")
    video_id = make_video(world, [drawn])
    store_file("videos/v/img-0-a.jpg")

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={
        "draft_version": 1, "scenes": [edit(drawn, from_index=0, image_prompt="")]}).json()

    stored = stored_video(video_id).scenes[0]
    assert (stored["image_prompt"], stored["image_key"], stored["image_prompt_used"]) == (None, None, None)
    assert (body["scenes"][0]["has_image"], body["scenes"][0]["needs_image"]) == (False, False)
    assert media_store().size("videos/v/img-0-a.jpg") is None
    # The slide is printed without the picture: a text slide, nothing to draw, nothing missing.
    assert body["render_problems"] == []


def test_a_dropped_picture_stays_on_disk_while_a_job_may_still_read_it(world):
    drawn = scene(world, kind="illustration", image_prompt="a queue", image_key="videos/v/img-0-a.jpg",
                  image_prompt_used="a queue")
    video_id = make_video(world, [drawn])
    store_file("videos/v/img-0-a.jpg")
    add_job(world, video_id, JobType.video_images, status=JobStatus.queued)

    client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={
        "draft_version": 1, "scenes": [edit(drawn, from_index=0, image_prompt="")]})

    assert stored_video(video_id).scenes[0]["image_key"] is None
    assert media_store().size("videos/v/img-0-a.jpg") is not None


# --- Voices and image models ---

def test_the_voices_and_image_models_are_listed_with_their_prices(world):
    body = client.get("/media-models", headers=headers(world["owner"])).json()

    assert body["default_voice"] == "voice-heygen" and body["default_image"] == "image-nano-banana"
    assert body["voices"][0] == {"alias": "voice-heygen", "label": "HeyGen Voice 1 Turbo",
                                 "price": "khoảng $0,03 một video 8 phút"}
    assert [m["alias"] for m in body["images"]] == ["image-nano-banana", "image-qwen"]
    assert client.get("/media-models", headers=headers(world["learner"])).status_code == 403


# --- Passages to cite ---

def test_the_passages_of_a_document_are_listed_in_order_with_their_labels(world):
    body = client.get(f"/documents/{world['doc']}/chunks", headers=headers(world["owner"])).json()

    assert body["total"] == 2
    assert [p["chunk_id"] for p in body["items"]] == [world["chunk"], world["chunk2"]]
    assert body["items"][0] == {"chunk_id": world["chunk"], "locator": "Chapter 5.pdf · trang 9",
                                "content": "FCFS runs jobs in order.", "snippet": "FCFS runs jobs in order."}


def test_a_passages_snippet_skips_the_pdfs_running_header_and_shows_the_match(world):
    # Live run: every snippet began "Chapter 5 CPU Scheduling › ... Operating System Concepts Silberschatz, Galvin
    # and Gagne ©2018", which used up the line before any of the passage's own words.
    header = "Operating System Concepts\nSilberschatz, Galvin and Gagne ©2018"
    pages = [("FCFS Scheduling (Cont.)", "Suppose that the processes arrive in the order: P2 , P3 , P1"),
             ("Round Robin (RR) Scheduling", ("Each process gets a small unit of CPU time (time quantum q), usually "
                                              "10-100 milliseconds. After this time has elapsed, the process is "
                                              "preempted and added to the end of the ready queue")),
             ("Dispatcher", "Dispatcher module gives control of the CPU to the process selected")]
    with SessionLocal() as db:
        doc = Document(course_id=world["course"], filename="Chapter 5_CPU Scheduling.pdf",
                       status=DocumentStatus.embedded, raw_text="x")
        db.add(doc)
        db.flush()
        db.add_all([Chunk(document_id=doc.id, course_id=world["course"], chunk_index=i,
                          content=f"Chapter 5 CPU Scheduling › {title}\n{10 + i}\n{header}\n{title}\n{text}",
                          source_ref=json.dumps({"spans": [{"page": 10 + i, "heading": title}]}))
                    for i, (title, text) in enumerate(pages)])
        db.commit()
        doc_id = doc.id
    h = headers(world["owner"])

    listed = client.get(f"/documents/{doc_id}/chunks", headers=h).json()["items"]
    found = client.get(f"/documents/{doc_id}/chunks", params={"q": "time quantum"}, headers=h).json()["items"]

    assert [p["snippet"][:24] for p in listed] == ["FCFS Scheduling (Cont.) ", "Round Robin (RR) Schedul",
                                                   "Dispatcher Dispatcher mo"]
    assert len(found) == 1 and "time quantum q" in found[0]["snippet"]
    assert "Silberschatz" not in found[0]["snippet"]
    # The passage itself is untouched: the snippet is only what the picker shows.
    assert found[0]["content"].startswith("Chapter 5 CPU Scheduling › Round Robin")


def test_the_passages_can_be_searched_and_paged(world):
    h = headers(world["owner"])
    found = client.get(f"/documents/{world['doc']}/chunks", params={"q": "round ROBIN"}, headers=h).json()
    second = client.get(f"/documents/{world['doc']}/chunks", params={"offset": 1, "limit": 1}, headers=h).json()
    literal = client.get(f"/documents/{world['doc']}/chunks", params={"q": "%"}, headers=h).json()

    assert ([p["chunk_id"] for p in found["items"]], found["total"]) == ([world["chunk2"]], 1)
    assert ([p["chunk_id"] for p in second["items"]], second["total"]) == ([world["chunk2"]], 2)
    # A search is for the text typed, not a pattern.
    assert literal == {"items": [], "total": 0}
    assert client.get(f"/documents/{world['doc']}/chunks", params={"limit": 51}, headers=h).status_code == 422


def test_the_passages_are_the_owners_only(world):
    path = f"/documents/{world['doc']}/chunks"
    refused = client.get(path, headers=headers(world["other"]))

    assert refused.status_code == 404
    assert refused.json() == client.get(f"/documents/{uuid.uuid4()}/chunks", headers=headers(world["other"])).json()
    assert client.get(path, headers=headers(world["learner"])).status_code == 403


def _too_many_illustrations(world):
    return [edit(scene(world), kind="illustration", image_prompt=f"picture {i}") for i in range(MAX_ILLUSTRATIONS + 1)]


@pytest.mark.parametrize("make_scenes", [
    lambda w: [edit(scene(w), bullet_citations=[[w["foreign_chunk"]], []])],
    lambda w: [edit(scene(w), narration_citations=[[str(uuid.uuid4())], []])],
    lambda w: [edit(scene(w), narration_citations=[["not-a-uuid"], []])],
    lambda w: [edit(scene(w), narration_citations=[[w["chunk"]]])],
    lambda w: [edit(scene(w), bullet_citations=[[w["chunk"]]] * 3)],
    lambda w: [],
    lambda w: [edit(scene(w))] * 11,
    _too_many_illustrations,
    lambda w: [edit(scene(w), from_index=0), edit(scene(w), from_index=0)],
    lambda w: [edit(scene(w), from_index=5)],
    lambda w: [edit(scene(w), title="  ")],
    lambda w: [edit(scene(w), kind="video")],
], ids=["foreign chunk", "unknown chunk", "not an id", "sentence without its sources", "too many bullet sources",
        "no scene", "11 scenes", "too many illustrations", "one scene twice", "no such scene", "blank title",
        "unknown kind"])
def test_an_unusable_edit_is_rejected_with_422_and_changes_nothing(world, make_scenes):
    video_id = make_video(world, [scene(world), scene(world)])

    response = client.put(f"/videos/{video_id}/scenes", json={"draft_version": 1, "scenes": make_scenes(world)},
                          headers=headers(world["owner"]))

    assert response.status_code == 422
    assert stored_video(video_id).draft_version == 1


def test_scenes_cannot_be_edited_while_the_script_is_generating(world):
    video_id = make_video(world, [], status=MediaStatus.generating)

    response = client.put(f"/videos/{video_id}/scenes", json={"draft_version": 1, "scenes": [edit(scene(world))]},
                          headers=headers(world["owner"]))

    assert response.status_code == 409


def test_an_edit_of_a_draft_that_changed_meanwhile_is_refused(world):
    video_id = make_video(world, draft_version=4)
    url, h = f"/videos/{video_id}/scenes", headers(world["owner"])

    stale = client.put(url, json={"draft_version": 3, "scenes": [edit(scene(world))]}, headers=h)
    fresh = client.put(url, json={"draft_version": 4, "scenes": [edit(scene(world))]}, headers=h)

    assert (stale.status_code, fresh.status_code) == (409, 200)
    assert fresh.json()["draft_version"] == 5


def test_a_lecturers_edit_is_cleaned_as_the_script_is(world):
    video_id = make_video(world)

    body = client.put(f"/videos/{video_id}/scenes", headers=headers(world["owner"]), json={"draft_version": 1, "scenes": [
        edit(scene(world), kind="illustration", image_prompt="queue of P_1 and P_2", formula="a+b", code="x = 1",
             narration="  Câu một.  \n\n Câu hai. ", narration_citations=[[world["chunk"]], [world["chunk"]]])]}).json()

    edited = body["scenes"][0]
    assert edited["image_prompt"] == "queue of P1 and P2"
    # A field the kind does not show is dropped, so the slide never has to choose.
    assert (edited["formula"], edited["code"], edited["table"]) == (None, None, None)
    assert edited["narration"] == "Câu một.\nCâu hai."


def test_a_diagram_scene_keeps_its_code_and_a_scene_of_another_kind_drops_it(world):
    video_id = make_video(world)
    code = 'flowchart LR\n  A["Đến"] --> B["Chạy"]'
    url, h = f"/videos/{video_id}/scenes", headers(world["owner"])

    saved = client.put(url, headers=h, json={"draft_version": 1, "scenes": [
        edit(scene(world), kind="diagram", diagram=f"  {code}\n")]}).json()["scenes"][0]
    assert (saved["kind"], saved["diagram"]) == ("diagram", code)
    # Changing the code makes the scene the lecturer's, as any field the slide shows does.
    assert saved["author"] == "lecturer"

    again = client.put(url, headers=h, json={"draft_version": 2, "scenes": [
        edit(saved, kind="text", diagram=code)]}).json()["scenes"][0]
    assert (again["kind"], again["diagram"]) == ("text", None)


# --- Drawing and rendering ---

def _illustrations(world, count=2):
    return [scene(world, kind="illustration", title=f"Hình {i}", image_prompt=f"picture {i}") for i in range(count)]


def test_drawing_queues_the_scenes_asked_for(world):
    video_id = make_video(world, _illustrations(world, 3))
    h = headers(world["owner"])

    all_missing = client.post(f"/videos/{video_id}/illustrations", json={}, headers=h)
    finish_jobs(video_id)
    one = client.post(f"/videos/{video_id}/illustrations", json={"scene_indexes": [1]}, headers=h)

    assert (all_missing.status_code, one.status_code) == (200, 200)
    with SessionLocal() as db:
        jobs = db.query(Job).filter(Job.payload["video_id"].astext == str(video_id)).order_by(Job.created_at).all()
        assert [(j.job_type, j.payload["scene_indexes"], j.document_id) for j in jobs] == [
            (JobType.video_images, [0, 1, 2], world["doc"]), (JobType.video_images, [1], world["doc"])]
        # The descriptions counted travel with the job, which draws them wherever their scenes move.
        assert [j.payload["prompts"] for j in jobs] == [["picture 0", "picture 1", "picture 2"], ["picture 1"]]
        assert db.get(User, world["owner"]).video_redraw_count == 4
        assert db.get(User, world["owner"]).generation_count == 0


def test_a_drawing_job_is_tried_once_so_a_failure_after_paying_does_not_draw_again(world):
    """Every model error is caught inside the job, so what reaches the queue comes after the
    pictures were paid for (storing them, the commit); a queue retry would draw them all again.
    The editor's Vẽ lại is the retry, and it counts the pictures."""
    video_id = make_video(world, _illustrations(world, 2))

    response = client.post(f"/videos/{video_id}/illustrations", json={}, headers=headers(world["owner"]))

    with SessionLocal() as db:
        assert db.get(Job, uuid.UUID(response.json()["job_id"])).max_attempts == 1


@pytest.mark.parametrize("scene_indexes", [[7], [-1]])
def test_drawing_a_scene_that_has_no_picture_to_draw_is_422(world, scene_indexes):
    video_id = make_video(world, [scene(world)] + _illustrations(world, 1))

    response = client.post(f"/videos/{video_id}/illustrations", json={"scene_indexes": scene_indexes},
                           headers=headers(world["owner"]))
    nothing = client.post(f"/videos/{video_id}/illustrations", json={"scene_indexes": [0]},
                          headers=headers(world["owner"]))

    assert (response.status_code, nothing.status_code) == (422, 422)
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).video_redraw_count == 0


def test_redraws_and_renders_have_a_daily_limit(world, monkeypatch):
    assert (settings.video_redraws_per_day, settings.video_renders_per_day) == (20, 10)
    monkeypatch.setattr(settings, "video_redraws_per_day", 3)
    monkeypatch.setattr(settings, "video_renders_per_day", 1)
    drawn = make_video(world, _illustrations(world, 2))
    rendered = make_video(world)
    h = headers(world["owner"])

    assert client.post(f"/videos/{drawn}/illustrations", json={}, headers=h).status_code == 200
    finish_jobs(drawn)
    assert client.post(f"/videos/{drawn}/illustrations", json={"scene_indexes": [0]}, headers=h).status_code == 200
    finish_jobs(drawn)
    over = client.post(f"/videos/{drawn}/illustrations", json={"scene_indexes": [1]}, headers=h)

    assert client.post(f"/videos/{rendered}/render", headers=h).status_code == 200
    finish_jobs(rendered)
    over_render = client.post(f"/videos/{rendered}/render", headers=h)

    assert over.status_code == 429 and "3 hình" in over.json()["detail"] and "ngày" in over.json()["detail"]
    assert over_render.status_code == 429 and "1 lần" in over_render.json()["detail"]
    with SessionLocal() as db:
        owner = db.get(User, world["owner"])
        assert (owner.video_redraw_count, owner.video_render_count, owner.generation_count) == (3, 1, 0)
        jobs = db.query(Job).filter(Job.payload["video_id"].astext.in_([str(drawn), str(rendered)])).count()
        assert jobs == 3


def test_a_render_queues_a_job(world):
    video_id = make_video(world)

    response = client.post(f"/videos/{video_id}/render", headers=headers(world["owner"]))

    assert response.status_code == 200
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(response.json()["job_id"]))
        assert (job.job_type, job.document_id, job.payload) == (
            JobType.video_render, world["doc"], {"video_id": str(video_id)})
    assert client.get(f"/videos/{video_id}", headers=headers(world["owner"])).json()["job"]["job_type"] == "video_render"


def test_render_is_refused_over_eight_minutes(world):
    long = scene(world, narration="Một câu rất dài. " * 300)
    video_id = make_video(world, [long, long])

    response = client.post(f"/videos/{video_id}/render", headers=headers(world["owner"]))

    assert response.status_code == 422
    minutes = f"{2 * len(long['narration']) / 15 / 60:.1f}".replace(".", ",")
    assert f"{minutes} phút" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).video_render_count == 0
        assert db.query(Job).filter(Job.payload["video_id"].astext == str(video_id)).count() == 0


def test_render_is_refused_while_a_picture_is_missing(world):
    video_id = make_video(world, [scene(world, kind="illustration", image_prompt="q", image_key="videos/gone.jpg")])

    response = client.post(f"/videos/{video_id}/render", headers=headers(world["owner"]))

    assert response.status_code == 422 and "vẽ lại" in response.json()["detail"]


@pytest.mark.parametrize("running", [JobType.video_images, JobType.video_render])
def test_drawing_and_rendering_wait_for_the_running_job(world, running):
    video_id = make_video(world, _illustrations(world, 1) + [scene(world)])
    add_job(world, video_id, running, JobStatus.queued)
    h = headers(world["owner"])

    assert client.post(f"/videos/{video_id}/illustrations", json={}, headers=h).status_code == 409
    assert client.post(f"/videos/{video_id}/render", headers=h).status_code == 409
    with SessionLocal() as db:
        owner = db.get(User, world["owner"])
        assert (owner.video_redraw_count, owner.video_render_count) == (0, 0)


@pytest.mark.parametrize("job_type", [JobType.video_images, JobType.video_render])
def test_a_failed_picture_or_render_is_retried_from_the_editor_only(world, job_type):
    video_id = make_video(world)
    job_id = add_job(world, video_id, job_type, JobStatus.failed)
    with SessionLocal() as db:
        other = Job(job_type=JobType.module_generation, step="generate", document_id=world["doc"],
                    status=JobStatus.failed, attempts=5, system_failure=True)
        db.add(other)
        db.commit()
        other_id = other.id

    by_lecturer = client.post(f"/jobs/{job_id}/retry", headers=headers(world["owner"]))
    by_admin = client.post("/admin/jobs/retry", json={"job_ids": [str(job_id), str(other_id)]},
                           headers=admin_headers())

    message = "Hãy bấm Vẽ lại / Dựng lại trong trình sửa video."
    assert (by_lecturer.status_code, by_lecturer.json()["detail"]) == (409, message)
    # One video job in the admin's batch does not hold back the others.
    assert by_admin.status_code == 200
    assert by_admin.json()["retried"] == [str(other_id)]
    assert by_admin.json()["skipped"] == [str(job_id)]
    assert by_admin.json()["reasons"] == {str(job_id): message}
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
        assert db.get(Job, other_id).status == JobStatus.queued


# --- Publishing, the file, deleting ---

def test_publish_needs_a_rendered_video(world):
    h = headers(world["owner"])
    unrendered = make_video(world)
    rendered = make_video(world, status=MediaStatus.ready, rendered_version=1, video_key="videos/r/v1.mp4")

    refused = client.post(f"/videos/{unrendered}/publish", headers=h)
    published = client.post(f"/videos/{rendered}/publish", headers=h)

    assert refused.status_code == 409
    assert published.status_code == 200 and published.json()["published_at"] is not None
    assert stored_video(unrendered).published_at is None
    hidden = client.post(f"/videos/{rendered}/unpublish", headers=h)
    assert hidden.status_code == 200 and hidden.json()["published_at"] is None
    assert stored_video(rendered).published_at is None


def test_the_rendered_file_is_sent_by_the_api_without_s3(world):
    h = headers(world["owner"])
    unrendered = make_video(world)
    rendered = make_video(world, rendered_version=1, video_key="videos/r/v1.mp4")
    store_file("videos/r/v1.mp4", b"mp4 bytes")

    assert client.get(f"/videos/{unrendered}/file", headers=h).status_code == 404
    link = client.get(f"/videos/{rendered}/file", headers=h).json()
    assert link == {"url": f"/videos/{rendered}/file/content", "via_api": True, "filename": "Chapter 5.mp4"}
    content = client.get(link["url"], headers=h)
    assert (content.status_code, content.content, content.headers["content-type"]) == (200, b"mp4 bytes", "video/mp4")


def test_the_rendered_file_is_a_short_lived_s3_link(world, monkeypatch):
    class FakeS3:
        def generate_presigned_url(self, operation, ExpiresIn, Params):
            return f"https://s3.example/{Params['Key']}?expires={ExpiresIn}"

    monkeypatch.setattr(videos_api, "media_store", lambda: S3UploadStore("bucket", "media/", client=FakeS3()))
    video_id = make_video(world, rendered_version=1, video_key="videos/r/v1.mp4")

    link = client.get(f"/videos/{video_id}/file", headers=headers(world["owner"])).json()

    assert link == {"url": "https://s3.example/media/videos/r/v1.mp4?expires=300", "via_api": False,
                    "filename": "Chapter 5.mp4"}
    assert client.get(f"/videos/{video_id}/file/content", headers=headers(world["owner"])).status_code == 404


def test_deleting_waits_for_a_running_job_then_removes_the_row_and_its_files(world):
    h = headers(world["owner"])
    keys = ["videos/d/v1.mp4", "videos/d/img-0.jpg", "videos/d/voice-0.wav"]
    video_id = make_video(world, [scene(world, kind="illustration", image_prompt="q", image_key=keys[1],
                                        voice_key=keys[2])], video_key=keys[0], rendered_version=1)
    for key in keys:
        store_file(key)
    add_job(world, video_id, JobType.video_render)

    assert client.delete(f"/videos/{video_id}", headers=h).status_code == 409
    assert all(media_store().size(k) is not None for k in keys)
    finish_jobs(video_id)
    assert client.delete(f"/videos/{video_id}", headers=h).status_code == 200

    assert client.get(f"/videos/{video_id}", headers=h).status_code == 404
    assert all(media_store().size(k) is None for k in keys)


def test_deleting_a_video_deletes_its_jobs_so_no_failure_outlives_it(world):
    h = headers(world["owner"])
    video_id = make_video(world, status=MediaStatus.failed)
    keep_id = make_video(world, status=MediaStatus.failed)
    script = add_job(world, video_id, JobType.video_script, JobStatus.failed)
    render = add_job(world, video_id, JobType.video_render, JobStatus.done)
    kept = add_job(world, keep_id, JobType.video_script, JobStatus.done)

    assert client.delete(f"/videos/{video_id}", headers=h).status_code == 200

    with SessionLocal() as db:
        assert db.get(Job, script) is None and db.get(Job, render) is None
        assert db.get(Job, kept) is not None
    listed = client.get("/documents", params={"course_id": str(world["course"])}, headers=h).json()
    assert all(d["failures"] == [] for d in listed)


def test_a_failed_picture_or_render_is_not_listed_on_its_document(world):
    """The video editor shows those errors, and only its own buttons can retry them, so the
    course page would list a healthy document as failed with a retry that always answers 409."""
    h = headers(world["owner"])
    video_id = make_video(world, status=MediaStatus.failed)
    add_job(world, video_id, JobType.video_images, JobStatus.failed)
    add_job(world, video_id, JobType.video_render, JobStatus.failed)
    script = add_job(world, video_id, JobType.video_script, JobStatus.failed)

    listed = client.get("/documents", params={"course_id": str(world["course"])}, headers=h).json()
    failures = next(d for d in listed if d["id"] == str(world["doc"]))["failures"]

    assert [(f["job_id"], f["job_type"]) for f in failures] == [(str(script), "video_script")]


def test_a_retried_script_makes_the_video_generating_again_so_it_cannot_be_edited_mid_run(world):
    h = headers(world["owner"])
    video_id = make_video(world, status=MediaStatus.failed, error="Model từ chối")
    job_id = add_job(world, video_id, JobType.video_script, JobStatus.failed)

    assert client.post(f"/jobs/{job_id}/retry", headers=h).status_code == 200

    video = stored_video(video_id)
    assert (video.status, video.error) == (MediaStatus.generating, None)
    assert client.put(f"/videos/{video_id}/scenes", json={"scenes": [edit(scene(world))], "draft_version": 1},
                      headers=h).status_code == 409


# --- The slide preview ---

def test_scene_preview_is_a_1280_by_720_png_for_the_owner_only(world):
    key = "videos/p/img-0.jpg"
    store_file(key, png())
    video_id = make_video(world, [
        scene(world, kind="illustration", image_prompt="a queue", image_key=key, image_prompt_used="a queue"),
        scene(world, title="Chỉ có chữ"),
    ])

    for index in (0, 1):
        response = client.get(f"/videos/{video_id}/scenes/{index}/preview.png", headers=headers(world["owner"]))
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, no-cache"
        assert Image.open(io.BytesIO(response.content)).size == (1280, 720)

    assert client.get(f"/videos/{video_id}/scenes/2/preview.png", headers=headers(world["owner"])).status_code == 404
    assert client.get(f"/videos/{video_id}/scenes/0/preview.png", headers=headers(world["other"])).status_code == 404
    assert client.get(f"/videos/{video_id}/scenes/0/preview.png", headers=headers(world["learner"])).status_code == 403


def test_an_unchanged_slide_is_answered_304_without_a_render_and_a_change_renders_it_again(world, monkeypatch):
    key = "videos/p/img-1.jpg"
    store_file(key, png())
    video_id = make_video(world, [scene(world, kind="illustration", image_prompt="a queue", image_key=key,
                                        image_prompt_used="a queue")])
    url, h = f"/videos/{video_id}/scenes/0/preview.png", headers(world["owner"])
    tag = client.get(url, headers=h).headers["etag"]

    # A reload: the browser sends its tag, and nothing is rendered, even with every renderer busy.
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    monkeypatch.setattr(videos_api, "PREVIEW_SLOTS", busy)
    again = client.get(url, headers={**h, "If-None-Match": tag})
    assert again.status_code == 304 and again.headers["etag"] == tag and again.content == b""
    monkeypatch.setattr(videos_api, "PREVIEW_SLOTS", threading.BoundedSemaphore(2))

    # A redrawn picture has a new key, so the same text is a new slide.
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.scenes = [{**video.scenes[0], "image_key": "videos/p/img-2.jpg"}]
        db.commit()
    store_file("videos/p/img-2.jpg", png())
    redrawn = client.get(url, headers={**h, "If-None-Match": tag})
    assert redrawn.status_code == 200 and redrawn.headers["etag"] != tag


def test_a_busy_preview_answers_within_two_seconds():
    assert videos_api.PREVIEW_WAIT_SECONDS == 1.5


def test_a_preview_is_503_while_the_renderers_are_busy(world, monkeypatch):
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    monkeypatch.setattr(videos_api, "PREVIEW_SLOTS", busy)
    monkeypatch.setattr(videos_api, "PREVIEW_WAIT_SECONDS", 0.05)
    video_id = make_video(world)

    response = client.get(f"/videos/{video_id}/scenes/0/preview.png", headers=headers(world["owner"]))

    assert response.status_code == 503 and "thử lại" in response.json()["detail"]


# --- Who may ---

def _routes(world, video_id):
    return [
        ("POST", "/videos", {"params": {"document_id": str(world["doc"])}, "json": {"output_language": "vi"}}),
        ("GET", f"/courses/{world['course']}/videos", {}),
        ("GET", f"/videos/{video_id}", {}),
        ("PUT", f"/videos/{video_id}/scenes", {"json": {"draft_version": 1, "scenes": [edit(scene(world))]}}),
        ("POST", f"/videos/{video_id}/illustrations", {"json": {}}),
        ("POST", f"/videos/{video_id}/render", {}),
        ("POST", f"/videos/{video_id}/publish", {}),
        ("POST", f"/videos/{video_id}/unpublish", {}),
        ("GET", f"/videos/{video_id}/file", {}),
        ("GET", f"/videos/{video_id}/file/content", {}),
        ("GET", f"/videos/{video_id}/scenes/0/preview.png", {}),
        ("DELETE", f"/videos/{video_id}", {}),
    ]


def test_another_lecturer_gets_404_everywhere(world):
    video_id = make_video(world, status=MediaStatus.ready, published=True, rendered_version=1,
                          video_key="videos/x/v1.mp4")
    store_file("videos/x/v1.mp4")
    h = headers(world["other"])

    for method, path, kwargs in _routes(world, video_id):
        assert client.request(method, path, headers=h, **kwargs).status_code == 404, (method, path)
    assert client.get(f"/videos/{uuid.uuid4()}", headers=h).json() == client.get(f"/videos/{video_id}", headers=h).json()
    assert stored_video(video_id).published_at is not None


def test_a_learner_cannot_call_author_routes(world):
    video_id = make_video(world, status=MediaStatus.ready, published=True, rendered_version=1,
                          video_key="videos/x/v1.mp4")
    h = headers(world["learner"])

    for method, path, kwargs in _routes(world, video_id):
        assert client.request(method, path, headers=h, **kwargs).status_code == 403, (method, path)


def _revise_model(monkeypatch, scenes, reply="Đã sửa."):
    """The model returns `scenes`, each (from_scene, title, bullets, sentences) with every text citing S1;
    the judge flags a scene that says WRONG."""
    from app.generation import fact_check
    from app.generation import video_script as generation
    seen = {}

    def complete(prompt, schema, **kwargs):
        seen["prompt"] = prompt
        return schema(reply=reply, scenes=[
            {"from_scene": origin, "kind": "summary", "title": title,
             "bullets": [{"text": b, "sources": ["S1"]} for b in bullets],
             "narration": [{"text": t, "sources": ["S1"] if t != "NO SOURCE" else []} for t in sentences]}
            for origin, title, bullets, sentences in scenes])
    monkeypatch.setattr(generation, "complete_structured", complete)
    monkeypatch.setattr(generation, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(fact_check, "judge_alias", lambda alias: "claude-fast")
    monkeypatch.setattr(fact_check, "check_items_against_sources", lambda batches, alias, language: [
        ["Câu 1: không có trong nguồn" if any("WRONG" in text for _, text in items) else None] for items, _ in batches])
    return seen


def test_a_script_is_revised_in_words_keeping_the_scenes_and_media_the_request_left_alone(world, monkeypatch):
    kept = scene(world, title="Giữ", author="lecturer", flags=["Ghi chú của giảng viên"],
                 image_key="videos/a.png", voice_key="videos/a.wav", voice_text_used="x")
    changed = scene(world, title="Đổi", voice_key="videos/b.wav", voice_text_used="old", voice_alias_used="voice-heygen")
    video_id = make_video(world, [kept, changed])
    same_sentences = kept["narration"].split("\n")
    seen = _revise_model(monkeypatch, [
        (1, "Giữ", kept["bullets"], same_sentences),
        (2, "Đổi", changed["bullets"], ["Câu mới về FCFS.", "Câu thứ hai."]),
        (None, "Mới", ["Ý một", "Ý hai"], ["WRONG claim.", "Một câu nữa."]),
        (None, "Không nguồn", ["Ý một", "Ý hai"], ["NO SOURCE"]),
    ])

    response = client.post(f"/videos/{video_id}/revise", headers=headers(world["owner"]),
                           json={"draft_version": 1, "message": "Viết lại cảnh 2, thêm một cảnh"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Đã sửa." and body["changed"] is True and body["draft_version"] == 2
    with SessionLocal() as db:
        stored = db.get(Video, video_id).scenes
    # The untouched scene is the same scene: author, flags, picture and voice.
    assert stored[0] == kept
    # A rewritten scene is the model's and keeps its old voice until the render compares what it read.
    assert stored[1]["author"] == "model" and stored[1]["narration"] == "Câu mới về FCFS.\nCâu thứ hai."
    assert (stored[1]["voice_key"], stored[1]["voice_text_used"]) == ("videos/b.wav", "old")
    # A new scene is fact-checked; one with no sourced sentence is dropped.
    assert [s["title"] for s in stored] == ["Giữ", "Đổi", "Mới"]
    assert stored[2]["flags"] == ["Câu 1: không có trong nguồn"] and stored[2]["voice_key"] is None
    assert "SAYS: FCFS chạy theo thứ tự đến. SOURCES: ['S1']" in seen["prompt"]


def test_a_revision_on_an_older_version_emptying_or_lengthening_the_video_is_refused(world, monkeypatch):
    video_id = make_video(world, [scene(world)])
    h = headers(world["owner"])
    _revise_model(monkeypatch, [])
    assert client.post(f"/videos/{video_id}/revise", headers=h, json={"draft_version": 0, "message": "x"}).status_code == 409
    assert client.post(f"/videos/{video_id}/revise", headers=h, json={"draft_version": 1, "message": "Xóa hết"}).status_code == 422

    long_sentences = ["Một câu thoại dài về cách FCFS xếp các tiến trình theo thứ tự đến trong hàng đợi."] * 12
    _revise_model(monkeypatch, [(None, f"Cảnh {n}", ["Ý một", "Ý hai"], long_sentences) for n in range(9)])
    longer = client.post(f"/videos/{video_id}/revise", headers=h, json={"draft_version": 1, "message": "Dài hơn"})
    assert longer.status_code == 422 and "8 phút" in longer.json()["detail"]
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert (video.draft_version, [s["title"] for s in video.scenes]) == (1, ["FCFS"])
