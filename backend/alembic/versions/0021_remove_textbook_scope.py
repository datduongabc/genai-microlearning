"""Ordinary document scope only; preserve modules, jobs and study records.

Legacy section-scoped modules cannot reproduce their former reference scope.
Downgrade restores empty section structures, not discarded section metadata.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_remove_textbook_scope"
down_revision = "0020_study_judge"
branch_labels = None
depends_on = None


def _failed_jobs_view(section_scope=False):
    section_filter = "AND n.section_id IS NOT DISTINCT FROM j.section_id " if section_scope else ""
    # Same projected columns: preserve dependent admin view, grants and retry function.
    op.execute(
        "CREATE OR REPLACE VIEW open_failed_jobs AS "
        "SELECT j.id, j.job_type, j.step, j.attempts, j.max_attempts, j.last_error, j.created_at, "
        "j.module_id, j.document_id, d.filename AS document_filename, c.name AS course_name, "
        "c.owner_id, u.name AS owner_name, u.email AS owner_email "
        "FROM jobs j JOIN documents d ON d.id = j.document_id "
        "JOIN courses c ON c.id = d.course_id JOIN users u ON u.id = c.owner_id "
        "WHERE j.status = 'failed' "
        "AND NOT EXISTS (SELECT 1 FROM jobs n WHERE n.document_id = j.document_id "
        f"AND n.job_type = j.job_type {section_filter}AND n.created_at > j.created_at)"
    )


def upgrade():
    _failed_jobs_view()
    # Detach first: deleting a section would otherwise cascade to its modules/jobs.
    for table in ("modules", "jobs"):
        op.drop_constraint(f"{table}_section_document_fkey", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_section_id", table_name=table)
        op.drop_column(table, "section_id")
    op.drop_table("document_sections")
    op.drop_column("documents", "kind")
    op.execute("DROP TYPE documentkind")
    op.execute("UPDATE jobs SET payload = payload - 'outline' - 'sections' "
               "WHERE job_type = 'document_pipeline'")


def downgrade():
    kind = postgresql.ENUM("lecture", "textbook", name="documentkind")
    kind.create(op.get_bind())
    op.add_column("documents", sa.Column("kind", postgresql.ENUM(
        "lecture", "textbook", name="documentkind", create_type=False),
        nullable=False, server_default="lecture"))
    op.create_table(
        "document_sections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.UUID(), sa.ForeignKey("document_sections.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer()), sa.Column("page_end", sa.Integer()),
        sa.Column("first_chunk_index", sa.Integer(), nullable=False),
        sa.Column("last_chunk_index", sa.Integer(), nullable=False),
        sa.Column("planner_concepts", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="ck_document_sections_level"),
        sa.CheckConstraint("first_chunk_index <= last_chunk_index", name="ck_document_sections_chunk_range"),
        sa.UniqueConstraint("id", "document_id", name="uq_document_sections_id_document_id"),
        sa.UniqueConstraint("document_id", "position", name="uq_document_sections_document_id_position"),
    )
    op.create_index("ix_document_sections_parent_id", "document_sections", ["parent_id"])
    op.execute("ALTER TABLE document_sections ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY document_sections_tenant_isolation ON document_sections "
               "USING (document_id IN (SELECT documents.id FROM documents))")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON document_sections TO app_runtime, app_worker")
    for table in ("modules", "jobs"):
        op.add_column(table, sa.Column("section_id", sa.UUID()))
        op.create_index(f"ix_{table}_section_id", table, ["section_id"])
        op.create_foreign_key(f"{table}_section_document_fkey", table, "document_sections",
                              ["section_id", "document_id"], ["id", "document_id"], ondelete="CASCADE")
    _failed_jobs_view(section_scope=True)
