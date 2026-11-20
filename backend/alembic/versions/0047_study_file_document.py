"""A study's reference file can be a copy of a course document, made once per study.

study_files.document_id names the document a file was copied from, so adding several packets
made from one document attaches one file. It is kept when the document goes (the copy is the
study's own).
"""
import sqlalchemy as sa

from alembic import op

revision = "0047_study_file_document"
down_revision = "0046_media_study_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("study_files", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_study_files_document", "study_files", "documents", ["document_id"], ["id"],
                          ondelete="SET NULL")
    op.create_index("ix_study_files_document_id", "study_files", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_study_files_document_id", "study_files")
    op.drop_constraint("fk_study_files_document", "study_files")
    op.drop_column("study_files", "document_id")
