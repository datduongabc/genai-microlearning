"""Research snapshots and rubric ratings, independent of pipeline evaluations."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
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
    status: Mapped[str] = mapped_column(default="draft", server_default="draft")
    created_at: Mapped[datetime] = created_at_column()


class StudyItem(Base):
    __tablename__ = "study_items"
    __table_args__ = (
        CheckConstraint("\"group\" IN ('human', 'llm_no_rag', 'rag_llm')", name="ck_study_items_group"),
        CheckConstraint("(\"group\" = 'rag_llm') = (module_id IS NOT NULL)", name="ck_study_items_module"),
        UniqueConstraint("study_id", "anon_code", name="uq_study_items_code",
                         deferrable=True, initially="DEFERRED"),
        UniqueConstraint("study_id", "module_id", name="uq_study_items_module"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    group: Mapped[str]
    anon_code: Mapped[str]
    # RESTRICT blocks source deletion while a RAG snapshot references it.
    module_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("modules.id", ondelete="RESTRICT"), index=True)
    packet: Mapped[dict] = mapped_column(JSONB)
    # Provenance stays outside the blinded packet. NULL means legacy/unknown.
    generator_model: Mapped[str | None]
    generation_models: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("(rater_kind = 'human' AND user_id IS NOT NULL) OR "
                        "(rater_kind = 'llm_judge' AND user_id IS NULL)", name="ck_ratings_rater"),
        UniqueConstraint("study_item_id", "user_id", name="uq_ratings_user"),
        Index("uq_ratings_llm_judge", "study_item_id", "judge_model", unique=True,
              postgresql_where=text("rater_kind = 'llm_judge'"), postgresql_nulls_not_distinct=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    study_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("study_items.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    rater_kind: Mapped[str]
    judge_alias: Mapped[str | None]
    judge_model: Mapped[str | None]
    # ModuleEvaluation's rubric: eight scored dimensions, then overall notes (criterion 9).
    accuracy: Mapped[dict] = mapped_column(JSONB)
    completeness: Mapped[dict] = mapped_column(JSONB)
    consistency: Mapped[dict] = mapped_column(JSONB)
    hallucination_score: Mapped[dict] = mapped_column(JSONB)
    slide_quality: Mapped[dict] = mapped_column(JSONB)
    quiz_quality: Mapped[dict] = mapped_column(JSONB)
    bloom_fidelity: Mapped[dict] = mapped_column(JSONB)
    query_alignment: Mapped[dict] = mapped_column(JSONB)
    overall_notes: Mapped[str] = mapped_column(Text)
    hallucination_findings: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = created_at_column()
