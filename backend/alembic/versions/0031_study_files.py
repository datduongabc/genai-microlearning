"""Reference files of a study: what the raters open beside a packet to judge it against.

The pipeline deletes a document's file once it is read, and a human-written packet has no document
at all, so the admin uploads the reference files to the study and picks one for each packet. Both
members of a RAG/no-RAG pair always get the same file, so the file says nothing about the group.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0031_study_files"
down_revision = "0030_document_awaiting_upload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("study_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("studies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="awaiting_upload"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('awaiting_upload', 'ready')", name="ck_study_files_status"),
    )
    op.create_index("ix_study_files_study_id", "study_files", ["study_id"])
    op.add_column("study_items", sa.Column(
        "reference_file_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("study_files.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_study_items_reference_file_id", "study_items", ["reference_file_id"])

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON study_files TO app_runtime")
    op.execute("ALTER TABLE study_files ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY study_files_admin ON study_files TO app_runtime "
               "USING (current_user_is_admin()) WITH CHECK (current_user_is_admin())")
    # The worker's hourly clean-up removes files whose upload never finished.
    op.execute("GRANT SELECT, DELETE ON study_files TO app_worker")
    op.execute("CREATE POLICY worker_processes_every_tenant ON study_files TO app_worker USING (true)")
    # A rater sees the item's file and nothing else of the source tables: a new column at the end
    # keeps CREATE OR REPLACE valid, and the file view lists only files a packet points to.
    op.execute("""
        CREATE OR REPLACE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet, reference_file_id FROM study_items WHERE study_invited(study_id)
    """)
    op.execute("""
        CREATE VIEW blind_study_files WITH (security_barrier = true) AS
        SELECT f.id, f.study_id, f.filename, f.size FROM study_files f
        WHERE f.status = 'ready' AND study_invited(f.study_id)
          AND EXISTS (SELECT 1 FROM study_items i WHERE i.reference_file_id = f.id)
    """)
    op.execute("GRANT SELECT ON blind_study_files TO app_runtime")


def downgrade() -> None:
    op.execute("DROP VIEW blind_study_files")
    op.execute("DROP VIEW blind_study_items")
    op.execute("""
        CREATE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet FROM study_items WHERE study_invited(study_id)
    """)
    op.execute("GRANT SELECT ON blind_study_items TO app_runtime")
    op.drop_index("ix_study_items_reference_file_id", table_name="study_items")
    op.drop_column("study_items", "reference_file_id")
    op.execute("REVOKE SELECT, DELETE ON study_files FROM app_worker")
    op.drop_index("ix_study_files_study_id", table_name="study_files")
    op.drop_table("study_files")
