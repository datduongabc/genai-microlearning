"""Admin study setup and invitation-only blind human ratings."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db import get_db
from app.generation.evaluation import ModuleContentInput, ModuleEvaluation
from app.jobs.queue import enqueue_job
from app.models import Job, JobStatus, JobType, Rating, Study, StudyItem, User
from app.study.items import assign_codes, existing_pair, shuffle_for_rater
from app.study.judge import validate_judge

router = APIRouter(prefix="/studies", tags=["studies"])
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


@router.post("", status_code=201, dependencies=[Depends(require_admin)])
def create_study(body: CreateStudy, db: Session = Depends(get_db)):
    study = Study(name=body.name, rubric_version=body.rubric_version)
    db.add(study)
    db.commit()
    db.refresh(study)
    return _study_out(study)


@router.get("", dependencies=[Depends(require_admin)])
def list_studies(db: Session = Depends(get_db)):
    return [_study_out(study) for study in db.scalars(select(Study).order_by(Study.created_at.desc(), Study.id))]


def _my_study(db: Session, study_id: uuid.UUID):
    study = db.execute(text("SELECT * FROM blind_studies WHERE id = :id"),
                       {"id": study_id}).mappings().first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return dict(study)


def _my_items(db: Session, study_id: uuid.UUID, user_id: uuid.UUID):
    items = db.execute(text("SELECT * FROM blind_study_items WHERE study_id = :id ORDER BY anon_code"),
                       {"id": study_id}).mappings().all()
    ratings = {rating.study_item_id: rating for rating in db.scalars(
        select(Rating).where(Rating.user_id == user_id,
                             Rating.study_item_id.in_([item["id"] for item in items])))}
    return [{"id": item["id"], "anon_code": item["anon_code"],
             "packet": ModuleContentInput.model_validate(item["packet"]).model_dump(),
             "rating": _rating_out(ratings[item["id"]], item["anon_code"]) if item["id"] in ratings else None}
            for item in items]


def _rating_out(rating: Rating, anon_code: str):
    return ModuleEvaluation(anon_id=anon_code, **{
        key: getattr(rating, key) for key in ModuleEvaluation.model_fields if key != "anon_id"
    }).model_dump()


@router.get("/mine")
def my_studies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [dict(row) for row in db.execute(text("""
        SELECT s.*, count(i.id) AS total, count(r.id) AS rated
        FROM blind_studies s LEFT JOIN blind_study_items i ON i.study_id = s.id
        LEFT JOIN ratings r ON r.study_item_id = i.id AND r.user_id = :uid
        GROUP BY s.id, s.name, s.rubric_version, s.status, s.created_at
        ORDER BY s.created_at DESC, s.id
    """), {"uid": user.id}).mappings()]


@router.get("/mine/{study_id}/items")
def my_items(study_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    study = _my_study(db, study_id)
    items = shuffle_for_rater(_my_items(db, study_id, user.id), study_id, user.id)
    return {"study": study, "items": items, "rated": sum(item["rating"] is not None for item in items),
            "total": len(items)}


@router.put("/mine/{study_id}/items/{item_id}/rating")
def save_rating(study_id: uuid.UUID, item_id: uuid.UUID, body: ModuleEvaluation,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.execute(text("SELECT anon_code FROM blind_study_items WHERE study_id=:sid AND id=:iid"),
                      {"sid": study_id, "iid": item_id}).mappings().first()
    if item is None:
        raise HTTPException(status_code=404, detail="Study item not found")
    if body.anon_id != item["anon_code"]:
        raise HTTPException(status_code=422, detail="Anonymous code does not match item")
    # Upsert serializes competing saves; the ratings trigger takes loading's study lock and
    # refuses a closed study or a rater whose invitation was withdrawn meanwhile.
    values = body.model_dump(exclude={"anon_id"})
    statement = insert(Rating).values(study_item_id=item_id, user_id=user.id, rater_kind="human", **values)
    try:
        db.execute(statement.on_conflict_do_update(constraint="uq_ratings_user", set_=values))
        db.commit()
    except (IntegrityError, ProgrammingError) as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "42501":
            raise HTTPException(status_code=404, detail="Study item not found") from exc
        if getattr(exc.orig, "sqlstate", None) == "23514":
            raise HTTPException(status_code=409, detail="Study is closed") from exc
        raise
    return body.model_dump()


class Raters(BaseModel):
    emails: list[EmailStr] = Field(max_length=100)


@router.get("/{study_id}/raters", dependencies=[Depends(require_admin)])
def list_raters(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return {"emails": study.rater_emails}


@router.put("/{study_id}/raters", dependencies=[Depends(require_admin)])
def set_raters(study_id: uuid.UUID, body: Raters, db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).with_for_update())
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    emails = sorted({str(email).lower() for email in body.emails})
    found = set(db.scalars(select(func.lower(User.email)).where(
        func.lower(User.email).in_(emails), User.is_active.is_(True))))
    if found != set(emails):
        raise HTTPException(status_code=422, detail="Every rater must have an active account")
    study.rater_emails = emails
    db.commit()
    return {"emails": emails}


@router.get("/{study_id}", dependencies=[Depends(require_admin)])
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


@router.post("/{study_id}/judge", status_code=202, dependencies=[Depends(require_admin)])
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
        model_info = validate_judge(items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = enqueue_job(db, job_type=JobType.study_judge, step="judge", study_id=study_id,
                      payload={"model_info": model_info,
                               "item_ids": [str(item.id) for item in items], "cursor": 0, "results": []})
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get("/{study_id}/judge/{job_id}", dependencies=[Depends(require_admin)])
def judge_status(study_id: uuid.UUID, job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.study_id != study_id or job.job_type != JobType.study_judge:
        raise HTTPException(status_code=404, detail="Judge job not found")
    return {"job_id": job.id, "status": job.status, "required_ratings_per_item": 2,
            "pending_item_ids": job.payload["item_ids"][job.payload.get("cursor", 0):],
            "total": len(job.payload["item_ids"]),
            "completed": job.payload.get("cursor", 0), "results": job.payload.get("results", []),
            "last_error": job.last_error}
