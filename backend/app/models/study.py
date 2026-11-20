"""Research snapshots and rubric ratings, independent of pipeline evaluations."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.models import created_at_column, uuid_pk


class Study(Base):
    __tablename__ = "studies"
    __table_args__ = (CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_studies_status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str]
    rubric_version: Mapped[str]
    rater_emails: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # Email -> the pair keys that rater rates (alembic 0044); a rater without an entry rates none (0045).
    rater_pairs: Mapped[dict[str, list[str]]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(default="draft", server_default="draft")
    created_at: Mapped[datetime] = created_at_column()


class StudyItem(Base):
    __tablename__ = "study_items"
    __table_args__ = (
        CheckConstraint("\"group\" IN ('human', 'llm_no_rag', 'rag_llm', 'flashcards', 'video')",
                        name="ck_study_items_group"),
        # A RAG packet loaded from its exported file has no module (alembic 0043).
        CheckConstraint("module_id IS NULL OR \"group\" = 'rag_llm'", name="ck_study_items_module"),
        UniqueConstraint("study_id", "anon_code", name="uq_study_items_code",
                         deferrable=True, initially="DEFERRED"),
        UniqueConstraint("study_id", "module_id", name="uq_study_items_module"),
        UniqueConstraint("study_id", "source_id", name="uq_study_items_source"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    group: Mapped[str]
    anon_code: Mapped[str]
    # RESTRICT blocks source deletion while a RAG snapshot references it.
    module_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("modules.id", ondelete="RESTRICT"), index=True)
    packet: Mapped[dict] = mapped_column(JSONB)
    # Both members of a RAG/no-RAG pair share the RAG module id, so judges rate them alike.
    pair_id: Mapped[uuid.UUID | None]
    # Provenance stays outside the blinded packet. NULL means legacy/unknown.
    generator_model: Mapped[str | None]
    generation_models: Mapped[list | None] = mapped_column(JSONB)
    # What the raters open beside the packet; the admin sets it, the same for both members of a pair.
    reference_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("study_files.id", ondelete="SET NULL"), index=True)
    # The flashcard set or video a media packet was taken from, and a rendered video's MP4 (alembic 0046).
    source_id: Mapped[uuid.UUID | None]
    media_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class StudyFile(Base):
    """A reference file of a study (alembic 0031). Its bytes are in study_file_store() under
    study_file_key(); the row exists from the upload request on, ready once the file is there."""
    __tablename__ = "study_files"
    __table_args__ = (CheckConstraint("status IN ('awaiting_upload', 'ready')", name="ck_study_files_status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(default="awaiting_upload", server_default="awaiting_upload")
    created_at: Mapped[datetime] = created_at_column()


_RUBRIC = "accuracy, completeness, consistency, hallucination_score, slide_quality, quiz_quality, bloom_fidelity, query_alignment"


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("(rater_kind = 'human' AND user_id IS NOT NULL) OR "
                        "(rater_kind = 'llm_judge' AND user_id IS NULL)", name="ck_ratings_rater"),
        UniqueConstraint("study_item_id", "user_id", name="uq_ratings_user"),
        CheckConstraint("rater_kind != 'llm_judge' OR judge_family IS NOT NULL",
                        name="ck_ratings_judge_family"),
        Index("uq_ratings_llm_judge", "study_item_id", "judge_family", unique=True,
              postgresql_where=text("rater_kind = 'llm_judge'")),
        # A module rating fills the eight rubric columns, a flashcard or video one `scores` (alembic 0046).
        CheckConstraint(f"(num_nonnulls({_RUBRIC}) = 8 AND scores IS NULL) OR "
                        f"(num_nonnulls({_RUBRIC}) = 0 AND scores IS NOT NULL)", name="ck_ratings_shape"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    study_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("study_items.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    rater_kind: Mapped[str]
    # What actually graded an llm_judge row: the gateway alias and the model it reported serving.
    judge_family: Mapped[str | None]
    judge_alias: Mapped[str | None]
    judge_model: Mapped[str | None]
    # ModuleEvaluation's rubric: eight scored dimensions, then overall notes (criterion 9).
    # Empty on a flashcard or video rating, whose criteria are in `scores` (alembic 0046).
    accuracy: Mapped[dict | None] = mapped_column(JSONB)
    completeness: Mapped[dict | None] = mapped_column(JSONB)
    consistency: Mapped[dict | None] = mapped_column(JSONB)
    hallucination_score: Mapped[dict | None] = mapped_column(JSONB)
    slide_quality: Mapped[dict | None] = mapped_column(JSONB)
    quiz_quality: Mapped[dict | None] = mapped_column(JSONB)
    bloom_fidelity: Mapped[dict | None] = mapped_column(JSONB)
    query_alignment: Mapped[dict | None] = mapped_column(JSONB)
    scores: Mapped[dict | None] = mapped_column(JSONB)
    overall_notes: Mapped[str] = mapped_column(Text)
    hallucination_findings: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = created_at_column()
