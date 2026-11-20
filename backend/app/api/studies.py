"""Admin study setup and invitation-only blind human ratings."""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.api.modules import GenerateRequest
from app.api.modules import _generate_without_retrieval as generate_control
from app.api.stored_files import download_link, send_stored
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.generation.evaluation import ModuleContentInput, ModuleEvaluation
from app.generation.limits import has_full_access
from app.generation.module_generation_pipeline import (
    IncompleteModuleError,
    control_payload,
    generation_payload_of,
    is_control,
    learning_objective_of,
    matching_control,
)
from app.jobs.queue import enqueue_job
from app.models import Course, Document, Job, JobStatus, JobType, Module, Rating, Study, StudyItem, User
from app.storage import media_store
from app.study.items import MODULE_GROUPS, assign_codes, existing_pair, shuffle_for_rater
from app.study.judge import validate_judge
from app.study.media import MediaEvaluation, MediaKind, media_candidates, media_item, next_code, packet_model
from app.study.results import human_ratings, judge_jsonl, rater_csv, study_results

router = APIRouter(prefix="/studies", tags=["studies"])
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CreateStudy(BaseModel):
    name: Nonempty
    rubric_version: Nonempty


class ImportedPacket(BaseModel):
    """One exported packet file (scripts/export_study_pair.py), loaded as it was rated and measured."""
    pair_id: uuid.UUID
    group: Literal["rag_llm", "llm_no_rag"]
    generator_model: str | None = None
    generation_models: list | None = None
    packet: ModuleContentInput


class LoadItems(BaseModel):
    module_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    human_packets: list[ModuleContentInput] = Field(default_factory=list, max_length=100)
    imported_packets: list[ImportedPacket] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.module_ids and not self.human_packets and not self.imported_packets:
            raise ValueError("Provide module_ids, human_packets or imported_packets")
        groups: dict[uuid.UUID, list[str]] = {}
        for item in self.imported_packets:
            groups.setdefault(item.pair_id, []).append(item.group)
        # A pair is one RAG packet and one no-RAG packet: anything else could not be compared.
        if any(sorted(g) != ["llm_no_rag", "rag_llm"] for g in groups.values()):
            raise ValueError("Each imported pair needs exactly one rag_llm and one llm_no_rag packet")
        return self


def _study_out(study: Study) -> dict:
    return {"id": study.id, "name": study.name, "rubric_version": study.rubric_version,
            "status": study.status, "created_at": study.created_at}


def _items(db: Session, study_id: uuid.UUID) -> list[StudyItem]:
    return list(db.scalars(select(StudyItem).where(StudyItem.study_id == study_id).order_by(StudyItem.anon_code)))


def _item_out(item: StudyItem) -> dict:
    return {"id": item.id, "anon_code": item.anon_code, "group": item.group,
            "module_id": item.module_id, "pair_id": item.pair_id, "packet": item.packet,
            "reference_file_id": item.reference_file_id}


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
    items = db.execute(text("""
        SELECT i.*, f.filename AS reference_filename, f.size AS reference_size
        FROM blind_study_items i LEFT JOIN blind_study_files f ON f.id = i.reference_file_id
        WHERE i.study_id = :id ORDER BY i.anon_code
    """), {"id": study_id}).mappings().all()
    ratings = {rating.study_item_id: rating for rating in db.scalars(
        select(Rating).where(Rating.user_id == user_id,
                             Rating.study_item_id.in_([item["id"] for item in items])))}
    return [{"id": item["id"], "anon_code": item["anon_code"], "kind": _kind(item["packet"]),
             "packet": _packet_out(item["packet"]),
             "reference": {"filename": item["reference_filename"], "size": item["reference_size"]}
             if item["reference_filename"] is not None else None,
             "rating": _rating_out(ratings[item["id"]], item["anon_code"], _kind(item["packet"]))
             if item["id"] in ratings else None}
            for item in items]


def _kind(packet: dict) -> str:
    """"module", "flashcards" or "video": a module packet carries no kind, as it always was."""
    return packet.get("kind", "module")


def _packet_out(packet: dict) -> dict:
    kind = _kind(packet)
    return (ModuleContentInput if kind == "module" else packet_model(kind)).model_validate(packet).model_dump()


def _rating_out(rating: Rating, anon_code: str, kind: str = "module"):
    if kind != "module":
        return MediaEvaluation(anon_id=anon_code, kind=kind, scores=rating.scores,
                               hallucination_findings=rating.hallucination_findings,
                               overall_notes=rating.overall_notes).model_dump()
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
def save_rating(study_id: uuid.UUID, item_id: uuid.UUID, raw: dict = Body(...),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.execute(text("SELECT anon_code, packet FROM blind_study_items WHERE study_id=:sid AND id=:iid"),
                      {"sid": study_id, "iid": item_id}).mappings().first()
    if item is None:
        raise HTTPException(status_code=404, detail="Study item not found")
    # The item says which rubric applies, so a module rating cannot be saved on a video or back.
    kind = _kind(item["packet"])
    try:
        body = (ModuleEvaluation.model_validate(raw) if kind == "module"
                else MediaEvaluation.model_validate({**raw, "kind": kind}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.anon_id != item["anon_code"]:
        raise HTTPException(status_code=422, detail="Anonymous code does not match item")
    # Upsert serializes competing saves; the ratings trigger takes loading's study lock and
    # refuses a closed study or a rater whose invitation was withdrawn meanwhile.
    values = body.model_dump(exclude={"anon_id", "kind"})
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
    # Email -> the pair keys (a pair's id, or a human packet's id) that rater rates.
    # A rater left out rates nothing yet (alembic 0045).
    assignments: dict[EmailStr, list[str]] = Field(default_factory=dict)


def pair_key(item: StudyItem) -> str:
    """The unit a rater is given: both packets of a pair, or a human packet alone (alembic 0044)."""
    return str(item.pair_id or item.id)


@router.get("/{study_id}/raters", dependencies=[Depends(require_admin)])
def list_raters(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return {"emails": study.rater_emails, "assignments": study.rater_pairs or {}}


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
    # Saving the invitations alone keeps each remaining rater's pairs.
    given = body.assignments if "assignments" in body.model_fields_set else study.rater_pairs or {}
    assignments = {str(email).lower(): sorted(set(keys)) for email, keys in given.items()
                   if keys and ("assignments" in body.model_fields_set or email in emails)}
    if not set(assignments) <= set(emails):
        raise HTTPException(status_code=422, detail="Only an invited rater can be given pairs")
    keys = {pair_key(item) for item in db.scalars(select(StudyItem).where(StudyItem.study_id == study_id))}
    if any(not set(given) <= keys for given in assignments.values()):
        raise HTTPException(status_code=422, detail="A given pair is not in this study")
    study.rater_emails = emails
    study.rater_pairs = assignments
    db.commit()
    return {"emails": emails, "assignments": assignments}


def _study_or_404(db: Session, study_id: uuid.UUID) -> Study:
    study = db.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return study


@router.get("/{study_id}/results", dependencies=[Depends(require_admin)])
def results(study_id: uuid.UUID, db: Session = Depends(get_db)):
    return study_results(db, _study_or_404(db, study_id))


@router.get("/{study_id}/results/raters/{number}.csv", dependencies=[Depends(require_admin)])
def rater_file(study_id: uuid.UUID, number: int, db: Session = Depends(get_db)):
    _study_or_404(db, study_id)
    raters = list(human_ratings(db, study_id).values())
    if not 1 <= number <= len(raters):
        raise HTTPException(status_code=404, detail="No rater with this number has rated yet")
    return Response(rater_csv(raters[number - 1]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rater-{number}.csv"'})


@router.get("/{study_id}/results/judge.jsonl", dependencies=[Depends(require_admin)])
def judge_file(study_id: uuid.UUID, db: Session = Depends(get_db)):
    _study_or_404(db, study_id)
    lines = judge_jsonl(db, study_id)
    if not lines:
        raise HTTPException(status_code=404, detail="The judge has not rated this study")
    return Response(lines, media_type="application/x-ndjson; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="judge.jsonl"'})


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
    # Ratings of flashcards or videos do not: module codes are reblinded, theirs are not.
    rated = db.scalar(select(Rating.id).join(StudyItem).where(
        StudyItem.study_id == study_id, Rating.rater_kind == "human", StudyItem.group.in_(MODULE_GROUPS)).limit(1))
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
    if {p.pair_id for p in body.imported_packets} & {item.pair_id for item in items}:
        raise HTTPException(status_code=409, detail="Pair already loaded")
    additions.extend(StudyItem(group="human", packet=packet.model_dump()) for packet in body.human_packets)
    additions.extend(StudyItem(group=p.group, pair_id=p.pair_id, packet=p.packet.model_dump(),
                               generator_model=p.generator_model, generation_models=p.generation_models)
                     for p in body.imported_packets)
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


class AddPair(BaseModel):
    module_id: uuid.UUID


def _pair_state(db: Session, module: Module) -> str:
    """Where a RAG module stands as a pair: its no-RAG control made ("ready"), being made
    ("generating"), failed the last time ("failed"), or never asked for ("none")."""
    payload = generation_payload_of(db, module.id)
    expected = control_payload(module.id, payload.get("model_alias"), payload.get("self_check", True),
                               payload.get("output_language", "vi"), payload.get("custom_instructions", ""))
    if matching_control(db, module.id, expected) is not None:
        return "ready"
    last = db.scalar(select(Job.status).where(
        Job.job_type == JobType.module_generation, Job.payload["paired_module_id"].astext == str(module.id),
    ).order_by(Job.created_at.desc()).limit(1))
    if last in (JobStatus.queued, JobStatus.running):
        return "generating"
    return "failed" if last == JobStatus.failed else "none"


def _admin_module(db: Session, module_id: uuid.UUID, admin: User) -> Module:
    module = db.scalar(select(Module).join(Course).where(Module.id == module_id, Course.owner_id == admin.id))
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    return module


@router.get("/{study_id}/candidates")
def pair_candidates(study_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """The admin's own complete RAG modules, each with where it stands as a pair of this study."""
    _study_or_404(db, study_id)
    loaded = set(db.scalars(select(StudyItem.module_id).where(
        StudyItem.study_id == study_id, StudyItem.module_id.is_not(None))))
    modules = db.scalars(select(Module).join(Course).where(
        Course.owner_id == current_user.id, Module.paired_module_id.is_(None)).order_by(Course.name, Module.title))
    out = []
    for module in modules:
        try:
            learning_objective_of(module)
        except IncompleteModuleError:
            continue
        if is_control(db, module.id):
            continue
        out.append({"id": module.id, "title": module.title, "course_name": module.course.name,
                    "state": "in_study" if module.id in loaded else _pair_state(db, module)})
    return out


@router.post("/{study_id}/pairs")
@limiter.limit("10/minute", key_func=user_or_address)
def add_pair(request: Request, study_id: uuid.UUID, body: AddPair, response: Response,
             db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Adds a RAG module and its no-RAG control as a pair. The control is made first when it does
    not exist yet (a paid generation, as the lecturer's own "generate without RAG"); the answer is
    then 202 and the pair is added by asking again once it is made."""
    _study_or_404(db, study_id)
    module = _admin_module(db, body.module_id, current_user)
    state = _pair_state(db, module)
    if state == "ready":
        response.status_code = 201
        return load_items(study_id, LoadItems(module_ids=[module.id]), db, current_user)
    response.status_code = 202
    if state == "generating":
        return {"state": "generating"}
    full_access = has_full_access(db, current_user)
    document = db.get(Document, module.document_id, with_for_update=True)
    generate_control(db, document, GenerateRequest(use_retrieval=False, paired_module_id=module.id),
                     current_user, full_access)
    return {"state": "generating"}


class AddMedia(BaseModel):
    kind: MediaKind
    id: uuid.UUID


@router.get("/{study_id}/media-candidates")
def media_options(study_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """The admin's own flashcard sets and videos that have something to rate."""
    _study_or_404(db, study_id)
    in_study = {item.source_id for item in _items(db, study_id) if item.source_id is not None}
    return media_candidates(db, current_user.id, in_study)


@router.post("/{study_id}/media", status_code=201)
def add_media(study_id: uuid.UUID, body: AddMedia, db: Session = Depends(get_db),
              current_user: User = Depends(require_admin)):
    """Adds one flashcard set or video as a packet of its own. Nothing is generated, and it can be
    added after rating has begun: no other packet changes code."""
    study = db.scalar(select(Study).where(Study.id == study_id).with_for_update())
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    if study.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft study can load items")
    items = _items(db, study_id)
    if any(item.source_id == body.id for item in items):
        raise HTTPException(status_code=409, detail="Already in this study")
    try:
        item = media_item(db, body.kind, body.id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    item.study_id = study_id
    item.anon_code = next_code(items, body.kind)
    item.packet = {**item.packet, "anon_id": item.anon_code}
    db.add(item)
    db.commit()
    return [_item_out(i) for i in _items(db, study_id)]


def _my_video(db: Session, study_id: uuid.UUID, item_id: uuid.UUID) -> tuple[str, str]:
    row = db.execute(text("SELECT anon_code, media_key FROM blind_study_items WHERE study_id=:sid AND id=:iid"),
                     {"sid": study_id, "iid": item_id}).mappings().first()
    if row is None or row["media_key"] is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return row["media_key"], f"{row['anon_code']}.mp4"


@router.get("/mine/{study_id}/items/{item_id}/video")
def my_video(study_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """Where a rater's browser plays a video packet: a short S3 link, or the API path below without S3.
    Named by its code, not its title, like the rest of the packet."""
    key, filename = _my_video(db, study_id, item_id)
    return download_link(media_store(), key, filename, f"/studies/mine/{study_id}/items/{item_id}/video/content")


@router.get("/mine/{study_id}/items/{item_id}/video/content")
def my_video_content(study_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    key, filename = _my_video(db, study_id, item_id)
    return send_stored(media_store(), key, filename)


@router.post("/{study_id}/judge", status_code=202, dependencies=[Depends(require_admin)])
def judge_study(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).with_for_update())
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    busy = db.scalar(select(Job.id).where(Job.study_id == study_id,
                     Job.status.in_([JobStatus.queued, JobStatus.running])).limit(1))
    if busy is not None:
        raise HTTPException(status_code=409, detail="A judge job is already running for this study")
    # The judge rates modules on their rubric; flashcards and videos are rated by people only.
    items = [item for item in _items(db, study_id) if item.group in MODULE_GROUPS]
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
    results = job.payload.get("results", [])
    return {"job_id": job.id, "status": job.status, "required_ratings_per_item": 2,
            "pending_item_ids": [r["item_id"] for r in results if r["status"] != "done"]
            + job.payload["item_ids"][len(results):],
            "total": len(job.payload["item_ids"]),
            "completed": sum(r["status"] == "done" for r in results), "results": results,
            "last_error": job.last_error}
