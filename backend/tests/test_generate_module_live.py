"""End-to-end integration test cho POST /modules/generate.

Khác với test_planner_cache.py (gọi thẳng hàm nội bộ, không LLM) và
test_course_ownership.py (không chạm generation), test này đi qua ĐÚNG
đường HTTP thật mà frontend dùng -- auth, ownership, retrieval, cả 7
component (Content/Slides/Example/Quiz/Practice/Explanation + LO), và
persist DB -- để bắt regression về wiring/serialization mà unit test
từng hàm generation riêng lẻ không thấy được.

generate_module giờ chạy qua job queue (POST /modules/generate chỉ enqueue,
trả về ngay job_id) -- TestClient không dùng `with` nên lifespan/
start_worker() không tự chạy nền (xem app/main.py), nên test tự "làm worker"
bằng cách gọi claim_next_job()/execute_job_step() trực tiếp trong 1 vòng lặp
tới khi job xong, thay vì poll qua HTTP như frontend thật làm.

Tốn quota LLM thật (Gemini free-tier) nên KHÔNG chạy mặc định (xem
addopts trong pyproject.toml). Chạy rõ ràng: `pytest -m live_llm`.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.ingestion.embeddings import embed_text
from app.jobs.queue import claim_next_job, execute_job_step, handle_job_error
from app.main import app
from app.models.models import Chunk, ComponentType, Document, DocumentStatus, JobStatus
from tests.auth_helpers import STRONG_PASSWORD

client = TestClient(app)

# Nội dung mẫu tự chứa (không phụ thuộc file trong available-learning-resources/,
# thư mục đó bị gitignore nên không đảm bảo tồn tại ở máy/CI khác). Đủ sâu để
# Planner/LO có nhiều hơn 1 mức Bloom khả dĩ (định nghĩa + so sánh + điều kiện
# áp dụng), không phải bài test trước đây trong session này (tránh trùng ngữ
# cảnh với các ví dụ few-shot đã có trong prompt).
SAMPLE_TEXT = """Tìm kiếm nhị phân (Binary Search) là thuật toán tìm một phần tử
trong mảng ĐÃ SẮP XẾP bằng cách liên tục chia đôi khoảng tìm kiếm. Thuật toán so
sánh phần tử ở giữa mảng với giá trị cần tìm: nếu bằng, trả về vị trí đó; nếu giá
trị cần tìm nhỏ hơn, tiếp tục tìm ở nửa trái; nếu lớn hơn, tiếp tục tìm ở nửa
phải. Độ phức tạp thời gian là O(log n), nhanh hơn nhiều so với tìm kiếm tuyến
tính (Linear Search) có độ phức tạp O(n), vì mỗi bước loại bỏ một nửa số phần
tử còn lại. Điều kiện tiên quyết bắt buộc: mảng đầu vào phải được sắp xếp
trước, nếu không thuật toán sẽ cho kết quả sai. Binary Search có thể cài đặt
theo 2 cách: đệ quy (recursive) hoặc lặp (iterative) -- cách lặp thường được
ưu tiên trong thực tế vì tránh overhead của lời gọi hàm đệ quy và tránh stack
overflow với mảng rất lớn."""


def _register_and_login(email: str) -> str:
    client.post("/auth/register", json={"email": email, "name": "Test", "password": STRONG_PASSWORD})
    response = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    return response.json()["access_token"]


def _seed_embedded_document(course_id: uuid.UUID) -> uuid.UUID:
    """Tạo document + chunk có embedding thật, bỏ qua bước parse file (PDF/PPTX/OCR)
    vì test này chỉ cần MỘT nội dung text thật để đi qua pipeline generate, không
    kiểm tra document_readers.py (đã có test riêng)."""
    db = SessionLocal()
    try:
        doc = Document(
            course_id=course_id, filename="binary_search.txt",
            status=DocumentStatus.embedded, raw_text=SAMPLE_TEXT,
        )
        db.add(doc)
        db.flush()
        db.add(Chunk(
            document_id=doc.id, course_id=course_id, chunk_index=0,
            content=SAMPLE_TEXT, embedding=embed_text(SAMPLE_TEXT),
        ))
        db.commit()
        return doc.id
    finally:
        db.close()


def _drive_job_to_completion(job_id: uuid.UUID, timeout_seconds: int = 1800) -> str:
    """Không có worker thread nền trong test (xem docstring module) -- tự
    claim+execute VÒNG LẶP job THẬT của chính nó (không job bất kỳ trong
    queue -- test khác có thể enqueue song song) tới khi done/failed."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        db = SessionLocal()
        try:
            from app.models import Job
            job = db.get(Job, job_id)
            status = job.status
        finally:
            db.close()

        if status in (JobStatus.done, JobStatus.failed):
            return status.value

        if status == JobStatus.queued:
            claimed = claim_next_job(f"test-worker-{uuid.uuid4().hex[:6]}")
            if claimed is not None and claimed.id == job_id:
                try:
                    execute_job_step(claimed)
                except Exception as err:
                    handle_job_error(claimed.id, err)
                continue
            if claimed is not None:
                # claim_next_job() không lọc theo job -- lỡ claim nhầm job
                # THẬT của người khác (vd 1 job khác đang chạy song song
                # trong cùng DB dev) thì phải trả lại ngay, không được để nó
                # kẹt "running" dưới worker_id giả của test.
                from app.models import Job

                db = SessionLocal()
                try:
                    other = db.get(Job, claimed.id)
                    other.status = JobStatus.queued
                    other.locked_at = None
                    other.locked_by = None
                    db.commit()
                finally:
                    db.close()
        time.sleep(0.5)
    raise TimeoutError(f"job {job_id} chưa xong sau {timeout_seconds}s")


@pytest.mark.live_llm
def test_generate_module_end_to_end_creates_all_components():
    suffix = uuid.uuid4().hex[:8]
    token = _register_and_login(f"live-llm-{suffix}@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    course = client.post("/courses", json={"name": f"Live LLM test {suffix}"}, headers=headers).json()
    document_id = _seed_embedded_document(uuid.UUID(course["id"]))

    generate_resp = client.post("/modules/generate", params={"document_id": str(document_id)}, headers=headers)
    assert generate_resp.status_code == 200, generate_resp.text
    job_id = uuid.UUID(generate_resp.json()["job_id"])

    final_status = _drive_job_to_completion(job_id)
    assert final_status == "done", "generate_module job phải kết thúc 'done', không phải 'failed'"

    job_resp = client.get(f"/modules/jobs/{job_id}", headers=headers)
    module_id = job_resp.json()["module_id"]
    assert module_id is not None

    detail = client.get(f"/modules/{module_id}", headers=headers).json()
    assert detail["title"]
    assert detail["learning_objective"]
    assert detail["bloom_level"] in (
        "remember", "understand", "apply", "analyze", "evaluate", "create",
    )
    types = {c["type"] for c in detail["components"]}
    assert types == {t.value for t in ComponentType}
    assert detail["sub_objectives"], "LO must produce at least one sub-objective"

    content = next(c for c in detail["components"] if c["type"] == "content")
    assert content["content"]["markdown"]
    assert content["content"]["claim_citations"], "Content must cite at least one claim"

    slides = next(c for c in detail["components"] if c["type"] == "slides")
    assert slides["content"]["slides"]
    for slide in slides["content"]["slides"]:
        assert len(slide["bullets"]) <= 3

    for component_type in ("quiz", "practice"):
        component = next(c for c in detail["components"] if c["type"] == component_type)
        assert len(component["questions"]) == 1
        question = component["questions"][0]
        assert question["stem"]
        assert len(question["choices"]["options"]) >= 2

    explanation = next(c for c in detail["components"] if c["type"] == "explanation")
    assert explanation["content"]["explanation_text"]

    # Không có user khác thấy được module này (đường ownership thật, không mock).
    other_token = _register_and_login(f"live-llm-other-{suffix}@example.com")
    forbidden = client.get(f"/modules/{module_id}", headers={"Authorization": f"Bearer {other_token}"})
    assert forbidden.status_code == 404
