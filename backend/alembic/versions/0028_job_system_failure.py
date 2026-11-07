"""Whether a failed job failed because of the system rather than its input.

A job that ran out of attempts on a provider, network or database error, or that a worker left stuck,
kept that exception's text as its only explanation, and the lecturer read it on the document:
"litellm.InternalServerError: ...". The text stays in last_error for the admin and the logs; the
lecturer is told instead that the system failed and that retrying later is what helps. A failure
caused by the input (ValueError raised with a message meant for the lecturer) is shown as before.
Existing failed jobs are left false, so they keep showing what they showed.
"""
import sqlalchemy as sa

from alembic import op

revision = "0028_job_system_failure"
down_revision = "0027_course_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("system_failure", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("jobs", "system_failure")
