"""Admin study setup; blind rating and results are delivered separately."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import get_db
from app.generation.evaluation import ModuleContentInput
from app.jobs.queue import enqueue_job
from app.models import Job, JobStatus, JobType, Rating, Study, StudyItem, User
from app.study.items import assign_codes, existing_pair
from app.study.judge import JUDGE_ALIAS, validate_judge

router = APIRouter(prefix="/studies", tags=["studies"], dependencies=[Depends(require_admin)])
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CreateStudy(BaseModel):
    name: Nonempty
    rubric_version: Nonempty


class LoadItems(BaseModel):
    module_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    human_packets: list[ModuleContentInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.module_ids and not self.human_packets:
            raise ValueError("Provide module_ids or human_packets")
        return self


def _study_out(study: Study) -> dict:
    return {"id": study.id, "name": study.name, "rubric_version": study.rubric_version,
            "status": study.status, "created_at": study.created_at}


def _items(db: Session, study_id: uuid.UUID) -> list[StudyItem]:
    return list(db.scalars(select(StudyItem).where(StudyItem.study_id == study_id).order_by(StudyItem.anon_code)))


def _item_out(item: StudyItem) -> dict:
    return {"id": item.id, "anon_code": item.anon_code, "group": item.group,
            "module_id": item.module_id, "packet": item.packet}


@router.post("", status_code=201)
def create_study(body: CreateStudy, db: Session = Depends(get_db)):
    study = Study(name=body.name, rubric_version=body.rubric_version)
    db.add(study)
    db.commit()
    db.refresh(study)
    return _study_out(study)


@router.get("")
def list_studies(db: Session = Depends(get_db)):
    return [_study_out(study) for study in db.scalars(select(Study).order_by(Study.created_at.desc(), Study.id))]


@router.get("/{study_id}")
def get_study(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return {**_study_out(study), "items": [_item_out(item) for item in _items(db, study_id)]}


@router.post("/{study_id}/items", status_code=201)
def load_items(study_id: uuid.UUID, body: LoadItems, db: Session = Depends(get_db),
               current_user: User = Depends(require_admin)):
    # Serialize draft loads before fetching items or swapping their deferred-unique codes.
    study = db.scalar(select(Study).where(Study.id == study_id).with_for_update())
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    # Only human ratings freeze the item set: the judge can be rerun over items loaded later.
    rated = db.scalar(select(Rating.id).join(StudyItem).where(
        StudyItem.study_id == study_id, Rating.rater_kind == "human").limit(1))
    judging = db.scalar(select(Job.id).where(Job.study_id == study_id,
                        Job.status.in_([JobStatus.queued, JobStatus.running])).limit(1))
    if study.status != "draft" or rated is not None or judging is not None:
        raise HTTPException(status_code=409, detail="Only an unrated draft study can load items")
    items = _items(db, study_id)
    existing = {item.module_id for item in items if item.module_id is not None}
    if len(set(body.module_ids)) != len(body.module_ids) or existing.intersection(body.module_ids):
        raise HTTPException(status_code=409, detail="RAG module already loaded or repeated")
    additions = []
    try:
        for module_id in body.module_ids:
            additions.extend(existing_pair(db, module_id, current_user.id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    additions.extend(StudyItem(group="human", packet=packet.model_dump()) for packet in body.human_packets)
    for item in additions:
        item.study_id = study_id
    assign_codes(items + additions)
    db.add_all(additions)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Study items conflict with existing data") from exc
    return [_item_out(item) for item in _items(db, study_id)]


@router.post("/{study_id}/judge", status_code=202)
def judge_study(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).with_for_update())
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    busy = db.scalar(select(Job.id).where(Job.study_id == study_id,
                     Job.status.in_([JobStatus.queued, JobStatus.running])).limit(1))
    if busy is not None:
        raise HTTPException(status_code=409, detail="A judge job is already running for this study")
    items = _items(db, study_id)
    if not items:
        raise HTTPException(status_code=409, detail="Study has no items")
    try:
        model, max_input_tokens = validate_judge(items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = enqueue_job(db, job_type=JobType.study_judge, step="judge", study_id=study_id,
                      payload={"judge_alias": JUDGE_ALIAS, "judge_model": model,
                               "max_input_tokens": max_input_tokens,
                               "item_ids": [str(item.id) for item in items], "cursor": 0, "results": []})
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get("/{study_id}/judge/{job_id}")
def judge_status(study_id: uuid.UUID, job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.study_id != study_id or job.job_type != JobType.study_judge:
        raise HTTPException(status_code=404, detail="Judge job not found")
    return {"job_id": job.id, "status": job.status, "judge_alias": job.payload["judge_alias"],
            "judge_model": job.payload["judge_model"], "total": len(job.payload["item_ids"]),
            "completed": job.payload.get("cursor", 0), "results": job.payload.get("results", []),
            "last_error": job.last_error}
