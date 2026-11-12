"""Narrated videos and flashcard sets of a document, and the job types that make them.

Both belong to a course through their document, with the composite foreign key `modules` uses, so a
row cannot name a document of another course. The course owner may do anything with them. A member of
the course (0027) reads only what the lecturer has published: a draft is the lecturer's until
`published_at` is set, and a member can change nothing.

The worker writes both (a script, cards, the rendered video), so it gets SELECT, INSERT and UPDATE and
the same pass-through policy every table under row level security has (0025).

New job types cannot be removed from an enum, so downgrade drops the tables and leaves the values,
as 0020 does.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0032_videos_flashcards"
down_revision = "0031_study_files"
branch_labels = None
depends_on = None

ME = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
OWNED = f"(SELECT courses.id FROM courses WHERE courses.owner_id = {ME})"
MEMBER = "(SELECT app_member_course_ids())"
TABLES = ("flashcard_sets", "videos")
JOB_TYPES = ("flashcard_generation", "video_script", "video_images", "video_render")


def _common(table: str) -> list:
    return [
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("focus", sa.String()),
        sa.Column("output_language", sa.String(), nullable=False),
        sa.Column("model_alias", sa.String(), nullable=False),
        sa.Column("status", postgresql.ENUM(name="mediastatus", create_type=False), nullable=False,
                  server_default="generating"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id", "course_id"], ["documents.id", "documents.course_id"],
                                name=f"{table}_document_course_fkey", ondelete="CASCADE"),
    ]


def upgrade():
    with op.get_context().autocommit_block():
        for value in JOB_TYPES:
            op.execute(f"ALTER TYPE jobtype ADD VALUE IF NOT EXISTS '{value}'")

    postgresql.ENUM("generating", "draft", "drawing", "rendering", "ready", "failed",
                    name="mediastatus").create(op.get_bind())

    op.create_table(
        "flashcard_sets", *_common("flashcard_sets"),
        sa.Column("cards", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "videos", *_common("videos"),
        sa.Column("voice_alias", sa.String(), nullable=False),
        sa.Column("image_alias", sa.String(), nullable=False),
        sa.Column("scenes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rendered_version", sa.Integer()),
        sa.Column("video_key", sa.String()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("cost", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("served_models", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text()),
    )
    for table in TABLES:
        op.create_index(f"ix_{table}_course_id", table, ["course_id"])
        op.create_index(f"ix_{table}_document_id", table, ["document_id"])
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_runtime")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO app_worker")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING (course_id IN {OWNED})")
        # SELECT only, so a member can neither change a draft nor see one.
        op.execute(f"CREATE POLICY {table}_member_read ON {table} FOR SELECT "
                   f"USING (course_id IN {MEMBER} AND published_at IS NOT NULL)")
        op.execute(f"CREATE POLICY worker_processes_every_tenant ON {table} TO app_worker "
                   "USING (true) WITH CHECK (true)")


def downgrade():
    for table in TABLES:
        op.drop_table(table)  # takes its policies, indexes and grants with it
    op.execute("DROP TYPE mediastatus")
