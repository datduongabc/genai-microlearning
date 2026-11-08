"""A document can exist before its file: status awaiting_upload.

The browser now sends the file straight to the upload store (a presigned S3 POST on AWS) instead of
through the API. The API creates the document first, hands out where to send the file, and queues
the pipeline only when the client reports the upload complete and the file is there. A document
left in this state is removed by the worker's hourly clean-up (app/jobs/maintenance.py).
"""

from alembic import op

revision = "0030_document_awaiting_upload"
down_revision = "0029_lti_roster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE documentstatus ADD VALUE IF NOT EXISTS 'awaiting_upload'")


def downgrade() -> None:
    # PostgreSQL cannot drop one value of an enum; the value stays, unused, once no row has it.
    op.execute("DELETE FROM documents WHERE status = 'awaiting_upload'")
