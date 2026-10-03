import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base

# Size of the vectors the embedding model is asked for (app/ingestion/embeddings.py). A vector of
# one size cannot be compared with one of another, so changing it needs a migration and a new vector
# for every chunk (scripts/reembed_chunks.py).
EMBEDDING_DIM = 768


class UserRole(str, enum.Enum):
    # Giảng viên dùng ứng dụng; học viên học trên Moodle sau khi giảng viên xuất bản nên không
    # có tài khoản nào ở đây. Admin là vai trò riêng, không phải giảng viên: chỉ quản lý tài
    # khoản giảng viên và theo dõi lỗi, không có môn học nào.
    teacher = "teacher"
    admin = "admin"


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    chunked = "chunked"
    embedded = "embedded"
    failed = "failed"


class DocumentKind(str, enum.Enum):
    lecture = "lecture"
    textbook = "textbook"


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
    role: Mapped[UserRole] = mapped_column(Enum(UserRole))
    # False: tài khoản bị khóa. Đọc lại từ DB ở mỗi request (get_current_user) nên khóa có hiệu
    # lực ngay, kể cả với token đang dùng. Khóa không xóa dữ liệu nào.
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Model giảng viên chọn sẵn cho các lượt sinh của mình. NULL nghĩa là chưa chọn, khi đó
    # dùng model trong cấu hình. Chỉ lưu alias của gateway, không lưu tên model thật: đổi
    # alias trỏ sang model khác là việc của devops/gateway/config.yaml, không phải của bảng
    # này. Không có khóa ngoại nào ràng buộc được nó, nên giá trị được kiểm ở biên API dựa
    # vào danh sách gateway đang phục vụ (app/llm/catalog.py).
    default_model_alias: Mapped[str | None] = mapped_column(nullable=True)
    # Tài khoản Moodle giảng viên đã tự chọn liên kết (POST /moodle/account/link). NULL: chưa liên
    # kết, kể cả khi Moodle có sẵn tài khoản cùng email.
    moodle_user_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str]
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # Set on first "Push to Moodle" (Week 11) so later pushes for this
    # course reuse the same Moodle course instead of creating a new one
    # each time. unique=True enforces a 1-1 mapping: one Moodle course must
    # never be linked from more than one course here, or pushes from two
    # different courses would collide inside the same Moodle course.
    moodle_course_id: Mapped[int | None] = mapped_column(nullable=True, unique=True)
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
    # The upload UI no longer asks and always sends lecture; textbook-specific
    # processing is deferred. Only a textbook gets an outline detected and
    # offered for picking sections. Slides and short notes keep working on
    # the whole document.
    kind: Mapped[DocumentKind] = mapped_column(
        Enum(DocumentKind), default=DocumentKind.lecture, server_default=DocumentKind.lecture.value
    )
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class DocumentSection(Base):
    """One chapter or section of a textbook, with the run of chunks it covers.

    A section is a row (not JSON on documents) so modules and jobs can point at
    it, the database can enforce that they belong to the same document, and the
    UI can show per-section state.
    """

    __tablename__ = "document_sections"
    __table_args__ = (
        # Target of the composite FKs on modules and jobs.
        UniqueConstraint("id", "document_id", name="uq_document_sections_id_document_id"),
        UniqueConstraint("document_id", "position", name="uq_document_sections_document_id_position"),
        CheckConstraint("level BETWEEN 1 AND 3", name="ck_document_sections_level"),
        CheckConstraint("first_chunk_index <= last_chunk_index", name="ck_document_sections_chunk_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str]
    level: Mapped[int]
    position: Mapped[int]
    page_start: Mapped[int | None] = mapped_column(nullable=True)
    page_end: Mapped[int | None] = mapped_column(nullable=True)
    first_chunk_index: Mapped[int]
    last_chunk_index: Mapped[int]
    # Same reason as documents.planner_concepts: the Planner must give the same
    # concepts every time for the same section.
    planner_concepts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


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
        ForeignKeyConstraint(
            ["section_id", "document_id"],
            ["document_sections.id", "document_sections.document_id"],
            name="modules_section_document_fkey",
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
    # NULL means the module was generated from the whole document. The composite
    # FK above keeps a module from pointing at a section of another document.
    section_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str]
    learning_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    bloom_level: Mapped[str | None] = mapped_column(nullable=True)
    # Enabling Objectives (GD2 — Terminal + Sub-objectives, mục Tuần 1 kiến
    # trúc GD2): 2-3 mục tiêu thành phần của learning_objective, mỗi cái ép
    # Content phải có 1 heading riêng bao phủ đúng khía cạnh đó.
    sub_objectives: Mapped[list | None] = mapped_column(JSONB, nullable=True)
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
        ForeignKeyConstraint(
            ["section_id", "document_id"],
            ["document_sections.id", "document_sections.document_id"],
            name="jobs_section_document_fkey",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[JobType] = mapped_column(Enum(JobType))
    # Indexed: the duplicate-generation guard in modules.py filters on this
    # column directly (Job.document_id == document_id), not covered by the
    # (status, run_after) composite below.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The section a module_generation job works on; NULL for whole-document jobs.
    section_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
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


class MoodleCourseClaim(Base):
    """Who first linked or created each Moodle course through this app. The application
    reaches Moodle with one account that sees every course, so this is what keeps one
    user from listing, linking and pushing into another's. Kept when a course is unlinked
    or deleted (see migration f3c8b5e1a7d4)."""

    __tablename__ = "moodle_course_claims"

    moodle_course_id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = created_at_column()

