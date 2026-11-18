"""LTI contexts remember when their member list was last tried, read or not.

A course whose list could not be read kept no trace of the attempt, so every check took it again
first. Fifty such courses filled every check, and the others were never read.
"""
import sqlalchemy as sa

from alembic import op

revision = "0039_lti_roster_checked_at"
down_revision = "0038_module_moodle_partial_cmids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lti_contexts", sa.Column("roster_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("GRANT UPDATE (roster_checked_at) ON lti_contexts TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (roster_checked_at) ON lti_contexts FROM app_worker")
    op.drop_column("lti_contexts", "roster_checked_at")
