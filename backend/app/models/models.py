import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.db import Base

# Size of the vectors the embedding model is asked for (app/ingestion/embeddings.py). A vector of
# one size cannot be compared with one of another, so changing it needs a migration and a new vector
# for every chunk (scripts/reembed_chunks.py).
EMBEDDING_DIM = 768


class DocumentStatus(str, enum.Enum):
    # Created, its file not yet in the upload store; the pipeline has not been queued.
    awaiting_upload = "awaiting_upload"
    uploaded = "uploaded"
    processing = "processing"
    chunked = "chunked"
    embedded = "embedded"
    failed = "failed"


class ChatRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class ComponentType(str, enum.Enum):
    content = "content"
    slides = "slides"
    example = "example"
    practice = "practice"
    quiz = "quiz"
    explanation = "explanation"


class JobType(str, enum.Enum):
    document_pipeline = "document_pipeline"
    module_generation = "module_generation"
    study_judge = "study_judge"
    flashcard_generation = "flashcard_generation"
    video_script = "video_script"
    video_images = "video_images"
    video_render = "video_render"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at_column():
    # timezone-aware everywhere: a naive timestamp depends on the server's time zone and a
    # browser parses it as local time, so it shows up shifted by the UTC offset.
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    hashed_password: Mapped[str]
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Deactivation ends access immediately while preserving account data and Moodle identity.
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Model giảng viên chọn sẵn cho các lượt sinh của mình. NULL nghĩa là chưa chọn, khi đó
    # dùng model trong cấu hình. Chỉ lưu alias của gateway, không lưu tên model thật: đổi
    # alias trỏ sang model khác là việc của devops/gateway/config.yaml, không phải của bảng
    # này. Không có khóa ngoại nào ràng buộc được nó, nên giá trị được kiểm ở biên API dựa
    # vào danh sách gateway đang phục vụ (app/llm/catalog.py).
    default_model_alias: Mapped[str | None] = mapped_column(nullable=True)
    # Confirmed Moodle identity; local account creation never links it automatically.
    moodle_user_id: Mapped[int | None] = mapped_column(nullable=True, unique=True)
    generation_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    generation_count: Mapped[int] = mapped_column(default=0, server_default="0")
    moodle_request_id: Mapped[str | None] = mapped_column(nullable=True)
    moodle_request_email: Mapped[str | None] = mapped_column(nullable=True)
    # False for an account an LMS made for a student: it chats in the courses it is a member of and
    # creates nothing. An instructor launch turns it on (app/lti/launch.py).
    can_author: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Chat messages today, counted for accounts that cannot author (settings.learner_daily_chat_limit).
    chat_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    chat_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Pictures drawn and videos rendered today: each is paid, so each has a daily limit of its own
    # (settings.video_redraws_per_day, video_renders_per_day; app/generation/limits.py).
    video_redraw_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    video_redraw_count: Mapped[int] = mapped_column(default=0, server_default="0")
    video_render_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    video_render_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Changes asked for in words today, of a module or a flashcard set (settings.edits_per_day).
    edit_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    edit_count: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = created_at_column()


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str]
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # Multiple app courses may use the same authorized Moodle course.
    moodle_course_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    # passive_deletes=True: on `db.delete(course)`, let Postgres' ON DELETE
    # CASCADE (migration 3d8f2a1c6e9b) handle children directly instead of
    # SQLAlchemy trying to NULL out their FK first (which fails outright --
    # those FK columns are NOT NULL).
    documents: Mapped[list["Document"]] = relationship(back_populates="course", passive_deletes=True)
    modules: Mapped[list["Module"]] = relationship(
        back_populates="course", passive_deletes=True, overlaps="document,modules"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="course", passive_deletes=True)


class Document(Base):
    __tablename__ = "documents"
    # Target of the composite FKs on chunks/modules, which is what lets the DB
    # guarantee their denormalised course_id matches this document's course.
    __table_args__ = (UniqueConstraint("id", "course_id", name="uq_documents_id_course_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str]
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.uploaded
    )
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_warnings: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # Cache cua run_planner() -- kien truc da ghi ro "1 lenh goi/tai lieu"
    # nhung implementation truoc day goi lai MOI LAN /modules/generate duoc
    # goi cho cung document, du khong can thiet. Ngoai ton lenh goi LLM thua,
    # day con la nguyen nhan that gay dao dong bloom_level giua cac lan sinh
    # module doc lap tren CUNG 1 tai lieu (Planner concept NAME on dinh qua
    # temperature=0.0, nhung phan summary van doi chu nhe moi lan goi -> query
    # retrieve() khac nhe -> chunk chon khac nhe -> LO co the ket luan khac,
    # xac nhan qua bai test lap lai 18 tai lieu, phat hien 3/17 doi ket qua).
    # Cache 1 lan, tai su dung moi lan sau -> cung 1 tai lieu luon cho ra
    # dung 1 top_concept + summary, xoa het bien so nay.
    planner_concepts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    course: Mapped["Course"] = relationship(back_populates="documents")
    # passive_deletes=True: same reasoning as Course.documents above.
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", passive_deletes=True)
    modules: Mapped[list["Module"]] = relationship(
        back_populates="document", passive_deletes=True, overlaps="course,modules"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    # (document_id, chunk_index): phục vụ expand_with_parent_context() --
    # equality trên document_id + range trên chunk_index, và cũng đủ cho các
    # chỗ chỉ lọc document_id đơn thuần (B-tree composite dùng được tiền tố
    # trái). Không thêm ivfflat/hnsw cho embedding: dense_search() luôn lọc
    # course_id TRƯỚC (hard metadata filter, mục 4 kiến trúc) rồi mới sort
    # cosine distance trên đúng subset đã lọc -- ở quy mô 1 course (vài chục
    # đến vài trăm chunk), đây là exact k-NN nhanh hơn và chính xác hơn ANN,
    # index ivfflat/hnsw chỉ có lợi khi tìm kiếm KHÔNG lọc trước trên tập rất
    # lớn (không phải pattern truy vấn thật của hệ thống này).
    __table_args__ = (
        Index("ix_chunks_document_id_chunk_index", "document_id", "chunk_index"),
        # Only chunks without a vector, for the workers' re-embedding pass (migration 0054).
        Index("ix_chunks_without_vector", "document_id", "chunk_index", postgresql_where=text("embedding IS NULL")),
        # (document_id, course_id) rather than document_id alone: course_id is a
        # denormalised copy of the document's course, and this makes Postgres
        # reject a chunk whose course_id disagrees with its document's.
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["documents.id", "documents.course_id"],
            name="chunks_document_course_fkey",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column()
    # Denormalized for cheap metadata filtering at retrieval time (mục 4 —
    # Multi-tenancy: hard filter by course_id before vector search).
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    # Position of this chunk within its document's chunk_units() output order
    # (0-based). Chunks for one document all commit in the same transaction,
    # so created_at ties them (same now()) and can't be used to find
    # neighbors -- same pitfall already hit once for Module components (Tuần
    # 10). Needed for Parent-Child Retrieval (mục 3.2 kiến trúc, GD2): finding
    # the chunks physically adjacent to a matched one to expand into a parent
    # context window.
    chunk_index: Mapped[int] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()

    document: Mapped["Document"] = relationship(back_populates="chunks")


class Module(Base):
    __tablename__ = "modules"
    # Same guarantee as Chunk: course_id must match the document's course.
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["documents.id", "documents.course_id"],
            name="modules_document_course_fkey",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Denormalized for the same reason as Chunk.course_id (cheap tenant filter
    # without a join) -- also the exact column the modules_tenant_isolation
    # RLS policy filters on (migration 881bca16e9bc), so the index isn't just
    # a read-path optimization, it's load-bearing for every RLS-checked query
    # against this table under the restricted app_runtime role.
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str]
    learning_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    bloom_level: Mapped[str | None] = mapped_column(nullable=True)
    # Enabling Objectives (GD2 — Terminal + Sub-objectives, mục Tuần 1 kiến
    # trúc GD2): 2-3 mục tiêu thành phần của learning_objective, mỗi cái ép
    # Content phải có 1 heading riêng bao phủ đúng khía cạnh đó.
    sub_objectives: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    generation_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Where the generation's time went: per-stage wall time, model calls and tokens, retrieval and
    # rerank time (app/timing.py). NULL for modules generated before it was recorded.
    generation_timings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    paired_module_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Successful calls in order, with requested aliases and actual response models.
    # NULL for old modules: their served models cannot be reconstructed from aliases.
    generation_models: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # When the module last went to Moodle: publishing again adds a second Book and two more quizzes,
    # so the page asks first, even after a reload or from another tab.
    moodle_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The activities a publish created before it failed ({"book_cmid": 101}): the next publish
    # creates only the missing ones instead of a second Book. NULL once a publish completes.
    moodle_partial_cmids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    document: Mapped["Document"] = relationship(back_populates="modules", overlaps="course,modules")
    # Both course and document write modules.course_id (the composite FK to documents
    # includes it); the values are forced equal by that FK, so the overlap is intended.
    course: Mapped["Course"] = relationship(back_populates="modules", overlaps="document,modules")
    # passive_deletes=True alongside delete-orphan: still deletes an item
    # removed from the collection in Python (orphan), but on `db.delete
    # (module)` lets ON DELETE CASCADE remove the rest in one DB round trip
    # instead of SQLAlchemy SELECTing then DELETEing each child itself.
    components: Mapped[list["ModuleComponent"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", passive_deletes=True
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", passive_deletes=True
    )


class ModuleComponent(Base):
    __tablename__ = "module_components"
    # Publishing and the preview pick "the" quiz/content/... of a module, so
    # a second one of the same type would make that choice arbitrary.
    __table_args__ = (UniqueConstraint("module_id", "type", name="uq_module_components_module_id_type"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    type: Mapped[ComponentType] = mapped_column(Enum(ComponentType))
    # Markdown for content/slides/example/explanation; structured JSON for quiz/practice.
    content: Mapped[dict] = mapped_column(JSONB)
    # Moved on by every write; a save names the one it was made from (alembic 0056).
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = created_at_column()

    module: Mapped["Module"] = relationship(back_populates="components")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="component", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Question.position",
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = uuid_pk()
    component_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("module_components.id", ondelete="CASCADE"), index=True
    )
    stem: Mapped[str] = mapped_column(Text)
    choices: Mapped[dict] = mapped_column(JSONB)
    answer_key: Mapped[str]
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Thứ tự trong thành phần: các câu của một module tạo cùng transaction nên created_at bằng nhau.
    position: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = created_at_column()

    component: Mapped["ModuleComponent"] = relationship(back_populates="questions")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    # e.g. "accuracy", "completeness", "consistency", "lo_alignment",
    # "groundedness_pass", "answerability_pass" — free-form per mục 9 (GD1).
    criterion: Mapped[str]
    score: Mapped[float]
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    module: Mapped["Module"] = relationship(back_populates="evaluations")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    # Whose chat it is: row level security shows a session to that person only (migration 0027).
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    course: Mapped["Course"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[ChatRole] = mapped_column(Enum(ChatRole))
    content: Mapped[str] = mapped_column(Text)
    # [{"chunk_id": "...", "source_ref": "..."}] -- chunk_id luôn được đối
    # chiếu lại với context_chunks THỰC SỰ truy xuất trước khi lưu (không tin
    # citation model tự echo ra, tránh hallucinate chunk_id không tồn tại).
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    applied_edit: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_column()

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


class Job(Base):
    """Durable job queue (Postgres-only, FOR UPDATE SKIP LOCKED -- không cần
    Redis, khớp triết lý "1 Postgres duy nhất" của kiến trúc). Chuyển
    upload_document()/generate_module() từ 1 HTTP request đồng bộ, chặn
    (đã thấy thật: 1 lần sinh module mất 10+ phút, không queue) sang xử lý
    nền, poll trạng thái qua API. Tham khảo thiết kế từ `jobs.py` (giữ ở
    `chunk_and_parse/`, code thật từ 1 dự án khác của tác giả) -- claim
    FOR UPDATE SKIP LOCKED, retry/backoff phân biệt lỗi tạm thời vs vĩnh
    viễn, sweep job "running" bị kẹt quá lâu (worker crash giữa chừng)."""
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    study_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[JobType] = mapped_column(Enum(JobType))
    # Indexed: the duplicate-generation guard in modules.py filters on this
    # column directly (Job.document_id == document_id), not covered by the
    # (status, run_after) composite below.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Chỉ có giá trị từ sau khi step tạo Module xong (module_generation) --
    # NULL trước đó, không phải lỗi.
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), nullable=True, index=True
    )
    step: Mapped[str] = mapped_column()
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued)
    # Trạng thái trung gian giữa các step (vd raw_text/chunk_units đã trích
    # cho document_pipeline) -- JSON thuần, không phải object Python.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # The steps of a module generation already done and paid for (app/generation/checkpoint.py, alembic 0051).
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=5)
    # timezone=True (khác các bảng khác trong file này, dùng datetime naive
    # mặc định) -- job_queue.py so sánh trực tiếp với datetime.now(UTC) (vd
    # claim_next_job() lọc run_after <= now, sweep_stuck_jobs() so locked_at
    # với cutoff); cột naive round-trip về Python cũng naive, so sánh với
    # datetime aware raise TypeError thẳng (bắt được qua test thật, không
    # phải đoán trước).
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the job fails: true when the system failed it (attempts used up on errors worth retrying,
    # or stuck), so the lecturer is not shown last_error's exception text. See migration 0028.
    system_failure: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = created_at_column()


class RefreshToken(Base):
    """A refresh token, stored as a sha256 hash so that a leaked database does not leak
    tokens that can be used (sha256 and not bcrypt: the token is random with high
    entropy, so it needs no deliberate slowing, and every refresh looks it up by this
    hash). revoked_at set means spent (replaced on refresh, single use) or logged out."""
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    # Copied unchanged to the successor on refresh, so a login ends here however often it
    # is refreshed.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

