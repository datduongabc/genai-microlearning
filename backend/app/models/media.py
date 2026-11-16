"""Narrated videos and flashcard sets generated from a document (migration 0032)."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, ForeignKeyConstraint, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db import Base
from app.models.models import created_at_column, uuid_pk


class MediaStatus(str, enum.Enum):
    generating = "generating"
    draft = "draft"
    drawing = "drawing"
    rendering = "rendering"
    ready = "ready"
    failed = "failed"


class _Media:
    """What a video and a flashcard set share: the document they come from and the lecturer's publish switch."""

    @declared_attr.directive
    def __table_args__(cls):
        # Same guarantee as Module: course_id must match the document's course.
        return (ForeignKeyConstraint(
            ["document_id", "course_id"], ["documents.id", "documents.course_id"],
            name=f"{cls.__tablename__}_document_course_fkey", ondelete="CASCADE"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Denormalized for the row level security policies, which filter on it.
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    focus: Mapped[str | None]
    output_language: Mapped[str]
    model_alias: Mapped[str]
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="mediastatus"), default=MediaStatus.generating, server_default="generating")
    # NULL keeps the item a draft the course's members cannot see.
    published_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class FlashcardSet(_Media, Base):
    __tablename__ = "flashcard_sets"

    # {front, back, citations: [chunk id], author: "model" | "lecturer", flag: str | None}
    cards: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # {usd, calls, priced_calls, prompt_tokens, completion_tokens} of the generation's text-model calls;
    # `usd` adds only the calls the gateway could price (see llm_cost in app/generation/media_common.py).
    cost: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    served_models: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")


class Video(_Media, Base):
    __tablename__ = "videos"

    voice_alias: Mapped[str]
    image_alias: Mapped[str]
    scenes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # The draft is edited while learners keep watching the rendered version.
    draft_version: Mapped[int] = mapped_column(default=1, server_default="1")
    rendered_version: Mapped[int | None]
    video_key: Mapped[str | None]
    duration_seconds: Mapped[float | None]
    # [{title, start_seconds, citations}] of the render, written with video_key: the draft's scenes
    # may have been edited since, and learners follow the file they watch.
    rendered_scenes: Mapped[list | None] = mapped_column(JSONB)
    # Per stage: script, images, voice.
    cost: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    served_models: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text)
