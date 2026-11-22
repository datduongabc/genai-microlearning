"""Admin study setup and invitation-only blind human ratings."""
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, contains_eager

from app.api.deps import get_current_user, require_admin
from app.api.modules import GenerateRequest
from app.api.modules import _generate_without_retrieval as generate_control
from app.api.stored_files import download_link, send_stored
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.generation.evaluation import RUBRIC_VERSION, ModuleContentInput, ModuleEvaluation, question_context
from app.generation.limits import has_full_access
from app.generation.module_generation_pipeline import (
    IncompleteModuleError,
    control_payload,
    generation_payload_of,
    is_control,
    learning_objective_of,
    normalize_generation_payload,
)
from app.jobs.queue import enqueue_job
from app.models import (
    Course,
    Document,
    FlashcardSet,
    Job,
    JobStatus,
    JobType,
    Module,
    Rating,
    Study,
    StudyItem,
    User,
    Video,
)
from app.storage import copy_file, media_store, study_file_store
from app.study.items import MODULE_GROUPS, assign_codes, existing_pair, shuffle_for_rater
from app.study.judge import validate_judge
from app.study.media import MediaEvaluation, MediaKind, media_candidates, media_item, next_code, packet_model
from app.study.references import attach_source
from app.study.results import criteria_of, human_ratings, judge_jsonl, media_csv, rater_csv, study_results

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studies", tags=["studies"])
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CreateStudy(BaseModel):
    # The rubric is not chosen: a new study is rated on the current one (RUBRIC_VERSION).
    name: Nonempty


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
    study = Study(name=body.name, rubric_version=RUBRIC_VERSION)
    db.add(study)
    db.commit()
    db.refresh(study)
    return _study_out(study)


@router.get("", dependencies=[Depends(require_admin)])
def list_studies(db: Session = Depends(get_db)):
    # A closed study is finished: its ratings stay for the export, but it is no longer worked on.
    return [_study_out(study) for study in db.scalars(select(Study).where(Study.status != "closed")
                                                       .order_by(Study.created_at.desc(), Study.id))]


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
    # As stored, not revalidated: a rating of an older rubric lacks the criteria added since.
    return {"anon_id": anon_code, **{key: getattr(rating, key) for key in ModuleEvaluation.model_fields
                                     if key != "anon_id" and getattr(rating, key) is not None}}


def check_current_rubric(rubric_version: str) -> None:
    """A study is rated on one rubric: one made under an older one takes no new module ratings."""
    if rubric_version != RUBRIC_VERSION:
        raise HTTPException(status_code=409, detail=(
            f"Nghiên cứu này dùng rubric {rubric_version}, không chấm thêm được theo rubric {RUBRIC_VERSION}. "
            "Hãy tạo nghiên cứu mới."))


@router.get("/mine")
def my_studies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [dict(row) for row in db.execute(text("""
        SELECT s.*, count(i.id) AS total, count(r.id) AS rated
        FROM blind_studies s LEFT JOIN blind_study_items i ON i.study_id = s.id
        LEFT JOIN ratings r ON r.study_item_id = i.id AND r.user_id = :uid
        WHERE s.status <> 'closed'
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
    if kind == "module":
        check_current_rubric(_my_study(db, study_id)["rubric_version"])
    try:
        body = (ModuleEvaluation.model_validate(raw, context=question_context(ModuleContentInput.model_validate(
                    item["packet"]))) if kind == "module"
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
    study = _study_or_404(db, study_id)
    raters = list(human_ratings(db, study_id).values())
    if not 1 <= number <= len(raters):
        raise HTTPException(status_code=404, detail="No rater with this number has rated yet")
    return Response(rater_csv(raters[number - 1], criteria_of(study)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rater-{number}.csv"'})


@router.get("/{study_id}/results/media.csv", dependencies=[Depends(require_admin)])
def media_file(study_id: uuid.UUID, db: Session = Depends(get_db)):
    _study_or_404(db, study_id)
    return Response(media_csv(db, study_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="flashcard-video.csv"'})


@router.get("/{study_id}/results/judge.jsonl", dependencies=[Depends(require_admin)])
def judge_file(study_id: uuid.UUID, db: Session = Depends(get_db)):
    _study_or_404(db, study_id)
    lines = judge_jsonl(db, study_id)
    if not lines:
        raise HTTPException(status_code=404, detail="The judge has not rated this study")
    return Response(lines, media_type="application/x-ndjson; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="judge.jsonl"'})


class ItemName(BaseModel):
    topic: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


@router.put("/{study_id}/items/{item_id}/topic", dependencies=[Depends(require_admin)])
def rename_item(study_id: uuid.UUID, item_id: uuid.UUID, body: ItemName, db: Session = Depends(get_db)):
    """The name raters see for a flashcard set or video, instead of its focus ("Toàn bộ tài liệu").
    A module packet keeps its topic: the judge rates query_alignment against it."""
    item = db.get(StudyItem, item_id)
    if item is None or item.study_id != study_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.group in MODULE_GROUPS:
        raise HTTPException(status_code=409, detail="A module packet keeps the topic it was made for")
    item.packet = {**item.packet, "topic": body.topic}
    db.commit()
    return [_item_out(i) for i in _items(db, study_id)]


@router.delete("/{study_id}/items/{item_id}", dependencies=[Depends(require_admin)])
def remove_item(study_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db)):
    """Takes a thing to rate out of the study: both packets of a pair, with the ratings given to
    them, and no rater is given it any more. The source module, flashcard set or video stays."""
    study = _study_or_404(db, study_id)
    item = db.get(StudyItem, item_id)
    if item is None or item.study_id != study_id:
        raise HTTPException(status_code=404, detail="Item not found")
    key = pair_key(item)
    copies = []
    for member in _items(db, study_id):
        if pair_key(member) == key:
            if member.media_key and member.media_key.startswith(VIDEO_COPY_PREFIX):
                copies.append(member.media_key)
            db.delete(member)
    before = study.rater_pairs or {}
    study.rater_pairs = {email: [k for k in keys if k != key] for email, keys in before.items()
                         if [k for k in keys if k != key]}
    # A rater whose last packet this was is no longer one and leaves the list. One added and not yet
    # given anything stays: the admin is about to give them something.
    emptied = {email for email, keys in before.items() if keys == [key]}
    study.rater_emails = [email for email in study.rater_emails or [] if email.lower() not in emptied]
    db.commit()
    # After the commit: a file without its row is harmless, a row without its file cannot play.
    for copy in copies:
        study_file_store().delete(copy)
    return [_item_out(i) for i in _items(db, study_id)]


@router.get("/{study_id}", dependencies=[Depends(require_admin)])
def get_study(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    items = _items(db, study_id)
    sources = _sources(db, items)
    return {**_study_out(study), "items": [{**_item_out(item), "source": sources.get(item.pair_id or item.source_id)}
                                           for item in items]}


def _sources(db: Session, items: list[StudyItem]) -> dict:
    """What each packet was made from, by its real name, for the admin (who is not blind): the
    RAG module of a pair, or the flashcard set or video. A human-written packet has none."""
    out = {}
    modules = {i.pair_id for i in items if i.pair_id}
    for module, course in db.execute(select(Module, Course.name).join(Course, Course.id == Module.course_id)
                                     .where(Module.id.in_(modules))):
        out[module.id] = {"title": module.title, "course_name": course, "created_at": module.created_at}
    for group, model in (("flashcards", FlashcardSet), ("video", Video)):
        ids = {i.source_id for i in items if i.group == group}
        for source, filename, course in db.execute(
                select(model, Document.filename, Course.name).join(Document, Document.id == model.document_id)
                .join(Course, Course.id == model.course_id).where(model.id.in_(ids))):
            out[source.id] = {"title": filename, "course_name": course, "created_at": source.created_at}
    return out


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
    for module_id in body.module_ids:
        attach_source(db, study_id, [i for i in additions if i.pair_id == module_id], db.get(Module, module_id).document_id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Study items conflict with existing data") from exc
    return [_item_out(item) for item in _items(db, study_id)]


class AddPair(BaseModel):
    module_id: uuid.UUID


def _pair_states(db: Session, modules: list[Module]) -> dict[uuid.UUID, str]:
    """Where each RAG module stands as a pair: its no-RAG control made with the same conditions
    ("ready"), being made ("generating"), failed the last time ("failed"), or never asked for
    ("none"). Three queries for any number of modules, as the admin's list can hold hundreds; the
    match is matching_control's, where `defaults || payload` is normalize_generation_payload."""
    ids = [m.id for m in modules]
    keys = [str(i) for i in ids]
    made: dict[str, list[dict]] = {}
    for paired, payload in db.execute(select(Module.paired_module_id, Module.generation_payload).where(
            Module.paired_module_id.in_(ids), Module.generation_payload.is_not(None))):
        made.setdefault(str(paired), []).append(normalize_generation_payload(payload))
    # Controls made before the conditions were kept on the module: their done job holds them.
    for payload, in db.execute(select(Job.payload).join(Module, Job.module_id == Module.id).where(
            Module.generation_payload.is_(None), Job.job_type == JobType.module_generation,
            Job.status == JobStatus.done, Job.payload["paired_module_id"].astext.in_(keys))):
        made.setdefault(payload["paired_module_id"], []).append(normalize_generation_payload(payload))
    paired_key = Job.payload["paired_module_id"].astext
    last = dict(db.execute(select(paired_key, Job.status).where(
        Job.job_type == JobType.module_generation, paired_key.in_(keys),
    ).distinct(paired_key).order_by(paired_key, Job.created_at.desc())).all())
    states = {}
    for module in modules:
        payload = generation_payload_of(db, module.id)
        expected = normalize_generation_payload(control_payload(
            module.id, payload.get("model_alias"), payload.get("self_check", True),
            payload.get("output_language", "vi"), payload.get("custom_instructions", "")))
        status = last.get(str(module.id))
        if expected in made.get(str(module.id), []):
            states[module.id] = "ready"
        elif status in (JobStatus.queued, JobStatus.running):
            states[module.id] = "generating"
        else:
            states[module.id] = "failed" if status == JobStatus.failed else "none"
    return states


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
    modules = db.scalars(select(Module).join(Course).options(contains_eager(Module.course)).where(
        Course.owner_id == current_user.id, Module.paired_module_id.is_(None)).order_by(Course.name, Module.title)).all()
    complete = []
    for module in modules:
        try:
            learning_objective_of(module)
        except IncompleteModuleError:
            continue
        if not is_control(db, module.id):
            complete.append(module)
    states = _pair_states(db, [m for m in complete if m.id not in loaded])
    return [{"id": module.id, "title": module.title, "course_name": module.course.name,
             "created_at": module.created_at, "state": "in_study" if module.id in loaded else states[module.id]}
            for module in complete]


@router.post("/{study_id}/pairs")
@limiter.limit("10/minute", key_func=user_or_address)
def add_pair(request: Request, study_id: uuid.UUID, body: AddPair, response: Response,
             db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Adds a RAG module and its no-RAG control as a pair. The control is made first when it does
    not exist yet (a paid generation, as the lecturer's own "generate without RAG"); the answer is
    then 202 and the pair is added by asking again once it is made."""
    _study_or_404(db, study_id)
    module = _admin_module(db, body.module_id, current_user)
    state = _pair_states(db, [module])[module.id]
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
    if item.media_key is not None:
        # The study keeps its own copy: the lecturer's next render, or deleting the video, removes
        # the file the video pointed at, and the packet must still play what was added.
        copy = f"{VIDEO_COPY_PREFIX}{uuid.uuid4().hex}.mp4"
        try:
            copy_file(media_store(), item.media_key, study_file_store(), copy)
        except Exception as exc:
            logger.exception("Could not copy video %s into study %s", body.id, study_id)
            raise HTTPException(status_code=503, detail="Không sao chép được video vào nghiên cứu. Hãy thử lại.") from exc
        item.media_key = copy
    db.add(item)
    attach_source(db, study_id, [item], db.get(FlashcardSet if body.kind == "flashcards" else Video, body.id).document_id)
    db.commit()
    return [_item_out(i) for i in _items(db, study_id)]


# A video packet's own copy, in the study's store. A key without it was added before copies were
# made and still names the lecturer's file in the media store.
VIDEO_COPY_PREFIX = "video-copies/"


def _video_store(key: str):
    return study_file_store() if key.startswith(VIDEO_COPY_PREFIX) else media_store()


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
    return download_link(_video_store(key), key, filename, f"/studies/mine/{study_id}/items/{item_id}/video/content")


@router.get("/mine/{study_id}/items/{item_id}/video/content")
def my_video_content(study_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    key, filename = _my_video(db, study_id, item_id)
    return send_stored(_video_store(key), key, filename)


@router.post("/{study_id}/judge", status_code=202, dependencies=[Depends(require_admin)])
def judge_study(study_id: uuid.UUID, db: Session = Depends(get_db)):
    study = db.scalar(select(Study).where(Study.id == study_id).with_for_update())
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    check_current_rubric(study.rubric_version)
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
