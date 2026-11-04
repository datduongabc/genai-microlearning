"""Thầy hướng dẫn yêu cầu thời gian trung bình của từng giai đoạn khi sinh một module. Mỗi module
lưu các span của lượt sinh ra nó, nên số liệu lấy từ chính các lượt sinh thật chứ không từ một
phép đo dựng riêng."""

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.models import Document, Module
from tests.integration.test_document_generation import document_world as generation_world
from tests.integration.test_document_generation import seen as offline_providers

document_world = generation_world
seen = offline_providers

STAGES = ["planner", "retrieval", "learning_objective", "content_retrieval", "content", "slides", "example",
          "questions"]


def test_a_generated_module_keeps_the_time_of_every_stage(document_world, seen):
    with SessionLocal() as db:
        module_id = pipeline.run_module_generation(db, db.get(Document, document_world["doc"])).id

    with SessionLocal() as db:
        timings = db.get(Module, module_id).generation_timings

    assert [s["stage"] for s in timings["spans"] if s["kind"] == "stage"] == STAGES
    assert set(timings["summary"]) == set(STAGES)
    assert timings["total_seconds"] >= sum(timings["summary"][s]["seconds"] for s in STAGES) - 1e-6
