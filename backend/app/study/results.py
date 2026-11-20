"""What the raters of a study gave: for the admin page, the rater CSVs and the judge lines.

One place for the numbering and the file formats, so the page, its downloads and
scripts/export_human_ratings.py name the same rater the same way and write the same columns.
Raters are numbered by email among those who rated at least one packet, as the export always did.
"""
import csv
import io
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.evaluation import DimensionScore, ModuleEvaluation
from app.models import Rating, Study, StudyItem, User
from app.study.media import CRITERIA as MEDIA_CRITERIA

# The scored criteria, in rubric order; overall_notes and the findings list are not scores.
CRITERIA = [name for name, field in ModuleEvaluation.model_fields.items() if field.annotation is DimensionScore]


def finding_notes(findings: list[dict] | None) -> str:
    return "\n".join(f"{f.get('field', '')}: {f.get('claim', '')} ({f.get('reason', '')})" for f in findings or [])


def pair_ids(items: list[StudyItem]) -> tuple[dict[str, str], list[str]]:
    """Pair code by anonymous code: each rag_llm packet with the one llm_no_rag packet of the same
    objective and sub-objectives. Also returns the codes that could not be paired, which keep their
    own code and so drop out of the paired tests."""
    by_objective: dict[tuple, dict[str, list[StudyItem]]] = {}
    for item in items:
        key = (item.packet.get("learning_objective"), tuple(item.packet.get("sub_objectives") or []))
        by_objective.setdefault(key, {}).setdefault(item.group, []).append(item)
    pairs = {item.anon_code: item.anon_code for item in items}
    unpaired = []
    for groups in by_objective.values():
        rag, no_rag = groups.get("rag_llm", []), groups.get("llm_no_rag", [])
        if len(rag) == 1 and len(no_rag) == 1:
            pairs[no_rag[0].anon_code] = rag[0].anon_code
        else:
            unpaired += [item.anon_code for item in rag + no_rag]
    return pairs, sorted(unpaired)


def human_ratings(db: Session, study_id: uuid.UUID) -> dict[str, list[tuple[StudyItem, Rating]]]:
    """Each rater's ratings by email, raters in numbering order and packets by code."""
    rows = db.execute(
        select(User.email, StudyItem, Rating)
        .join(StudyItem, Rating.study_item_id == StudyItem.id)
        .join(User, Rating.user_id == User.id)
        .where(StudyItem.study_id == study_id, Rating.rater_kind == "human")
        .order_by(User.email, StudyItem.anon_code)
    ).all()
    by_email: dict[str, list[tuple[StudyItem, Rating]]] = {}
    for email, item, rating in rows:
        by_email.setdefault(email, []).append((item, rating))
    return dict(sorted(by_email.items()))


def judge_ratings(db: Session, study_id: uuid.UUID) -> list[tuple[StudyItem, Rating]]:
    return list(db.execute(
        select(StudyItem, Rating)
        .join(Rating, Rating.study_item_id == StudyItem.id)
        .where(StudyItem.study_id == study_id, Rating.rater_kind == "llm_judge")
        .order_by(StudyItem.anon_code, Rating.judge_alias)
    ).tuples())


def evaluation_of(rating: Rating, anon_code: str) -> dict:
    return ModuleEvaluation(anon_id=anon_code, **{
        key: getattr(rating, key) for key in ModuleEvaluation.model_fields if key != "anon_id"
    }).model_dump()


def rater_csv(ratings: list[tuple[StudyItem, Rating]]) -> str:
    """One rater's file in the format of docs/rubric.md section 5, which analyze_study.py reads."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["anon_id", *CRITERIA, "hallucination_notes"])
    # Module packets only: analyze_study.py compares RAG to no-RAG on the module rubric.
    for item, rating in ratings:
        if rating.scores is not None:
            continue
        writer.writerow([item.anon_code, *(getattr(rating, c)["score"] for c in CRITERIA),
                         finding_notes(rating.hallucination_findings)])
    return out.getvalue()


def rater_names(db: Session, emails) -> dict[str, str]:
    """Each rater by name for the admin, who is not blind to who rated; by email when two share one."""
    names = dict(db.execute(select(User.email, User.name).where(User.email.in_(list(emails)))).all())
    taken = [names.get(email) for email in emails]
    return {email: names.get(email) if names.get(email) and taken.count(names.get(email)) == 1 else email
            for email in emails}


MEDIA_COLUMNS = list(dict.fromkeys(c for criteria in MEDIA_CRITERIA.values() for c in criteria))


def media_csv(db: Session, study_id: uuid.UUID) -> str:
    """Every rating of a flashcard set or video, one row each. Apart from the rater files, which
    hold the module rubric analyze_study.py reads. With a BOM, so Excel shows the Vietnamese."""
    humans = human_ratings(db, study_id)
    names = rater_names(db, humans)
    out = io.StringIO()
    out.write("\ufeff")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["anon_id", "kind", "topic", "rater", *MEDIA_COLUMNS, "hallucination_notes", "overall_notes"])
    rows = [(item, email, rating) for email, ratings in humans.items() for item, rating in ratings if rating.scores is not None]
    for item, email, rating in sorted(rows, key=lambda row: (row[0].anon_code, row[1])):
        writer.writerow([item.anon_code, item.group, item.packet.get("topic", ""), names[email],
                         *(rating.scores.get(c, {}).get("score", "") for c in MEDIA_COLUMNS),
                         finding_notes(rating.hallucination_findings), rating.overall_notes])
    return out.getvalue()


def judge_jsonl(db: Session, study_id: uuid.UUID) -> str:
    """The judge's ratings in run_judge.py's line format, with the same codes as the rater files."""
    rows = judge_ratings(db, study_id)
    if not rows:
        return ""
    pairs, _ = pair_ids(list(db.scalars(select(StudyItem).where(StudyItem.study_id == study_id))))
    return "".join(json.dumps({
        "anon_id": item.anon_code, "pair_id": pairs[item.anon_code], "group": item.group,
        "judge": rating.judge_alias, "judge_model": rating.judge_model,
        "evaluation": evaluation_of(rating, item.anon_code),
    }, ensure_ascii=False) + "\n" for item, rating in rows)


def _rating_view(rater: str, kind: str, rating: Rating) -> dict:
    return {
        "rater": rater, "kind": kind,
        "scores": rating.scores if rating.scores is not None else {c: getattr(rating, c) for c in CRITERIA},
        "findings": rating.hallucination_findings or [],
        "notes": rating.overall_notes,
    }


def study_results(db: Session, study: Study) -> dict:
    """Everything the admin page shows. The group of each packet is shown: the admin runs the
    study, only the raters are blind to it."""
    items = list(db.scalars(select(StudyItem).where(StudyItem.study_id == study.id).order_by(StudyItem.anon_code)))
    pairs, unpaired = pair_ids(items)
    humans = human_ratings(db, study.id)
    numbers = {email: n for n, email in enumerate(humans, start=1)}
    names = rater_names(db, sorted({email.lower() for email in study.rater_emails or []} | set(humans)))
    by_item: dict[uuid.UUID, list[dict]] = {item.id: [] for item in items}
    for email, ratings in humans.items():
        for item, rating in ratings:
            by_item[item.id].append(_rating_view(names[email], "human", rating))
    for item, rating in judge_ratings(db, study.id):
        by_item[item.id].append(_rating_view(rating.judge_alias or "Mô hình chấm", "llm_judge", rating))

    invited = {email.lower() for email in study.rater_emails or []}
    given = study.rater_pairs or {}
    def assigned(email: str) -> int:
        # How many packets this rater is to rate: those of the pairs given them, none until then.
        keys = given.get(email, [])
        return sum(str(item.pair_id or item.id) in keys for item in items)
    raters = [
        {"email": email, "name": names[email], "number": numbers.get(email), "rated": len(humans.get(email, [])),
         # The rater file holds module ratings only; with none it would be empty.
         "module_rated": sum(rating.scores is None for _, rating in humans.get(email, [])),
         "invited": email in invited, "assigned": assigned(email)}
        for email in sorted(invited | set(humans))
    ]
    return {
        "criteria": CRITERIA,
        "total": len(items),
        "raters": raters,
        "has_judge": any(r["kind"] == "llm_judge" for rs in by_item.values() for r in rs),
        "has_media_ratings": any(rating.scores is not None for ratings in humans.values() for _, rating in ratings),
        "unpaired": unpaired,
        "items": [{
            "id": item.id, "anon_code": item.anon_code, "group": item.group,
            "pair_code": pairs[item.anon_code], "topic": item.packet.get("topic", ""),
            "criteria": list(MEDIA_CRITERIA.get(item.group, CRITERIA)),
            "ratings": by_item[item.id],
        } for item in items],
    }
