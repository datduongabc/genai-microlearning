import random
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.generation.module_generation_pipeline import (
    control_payload,
    generation_payload_of,
    learning_objective_of,
)
from app.generation.study_packets import module_packet, reference_chunks
from app.models import Job, JobStatus, JobType, Module, StudyItem


def assign_codes(items: list[StudyItem]) -> None:
    """Reblind the whole draft, so incrementally loaded groups cannot be inferred from codes."""
    shuffled = list(items)
    random.SystemRandom().shuffle(shuffled)
    for number, item in enumerate(shuffled, 1):
        item.anon_code = f"M{number:02d}"
        item.packet = {**item.packet, "anon_id": item.anon_code}


def existing_pair(db: Session, module_id: uuid.UUID) -> list[StudyItem]:
    """Snapshot an existing matched pair; importing never generates a control or calls the LLM."""
    db.execute(text("SELECT set_config('app.study_source_module', :mid, true), "
                    "set_config('app.study_control_module', '', true)"), {"mid": str(module_id)})
    module = db.get(Module, module_id)
    if module is None:
        raise LookupError("Module not found")
    payload = generation_payload_of(db, module_id)
    if payload.get("use_retrieval", True) is False:
        raise ValueError("Source must be a RAG module, not a no-RAG control")
    learning_objective_of(module)
    expected = control_payload(module_id, payload.get("model_alias"), payload.get("self_check", True),
                               payload.get("output_language", "vi"), payload.get("custom_instructions", ""))
    control_job = db.scalar(select(Job).where(
        Job.job_type == JobType.module_generation, Job.status == JobStatus.done,
        Job.module_id.is_not(None), Job.payload == expected,
    ).order_by(Job.created_at.desc(), Job.id))
    if control_job is None:
        raise FileNotFoundError("Matching completed no-RAG control not found; generate it before loading the study")
    db.execute(text("SELECT set_config('app.study_control_module', :mid, true)"),
               {"mid": str(control_job.module_id)})
    control = db.get(Module, control_job.module_id)
    if control is None:
        raise FileNotFoundError("Paired control module not found")
    sources = reference_chunks(db, module)
    return [
        StudyItem(group="rag_llm", module_id=module.id, packet=module_packet(module, sources).model_dump()),
        StudyItem(group="llm_no_rag", packet=module_packet(control, sources).model_dump()),
    ]
