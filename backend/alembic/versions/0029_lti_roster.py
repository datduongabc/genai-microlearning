"""Course members follow the LMS course: LTI Names and Role Provisioning Services 2.0.

A student joined a course here by launching it, and nothing ever took them out: someone removed from
the Moodle course kept chatting with its material until their login ran out, 14 days later. The
worker now reads each course's member list from the LMS and removes the members it no longer lists
as active (app/lti/roster.py).

- lti_platforms.token_url: the LMS's OAuth 2 token endpoint, entered with the registration.
- lti_contexts.memberships_url: the member-list address of that LMS course, from the latest launch
  that carried one (the request role may update that column only); roster_synced_at: when it was read.
- The worker reads platforms, contexts and identities, stamps roster_synced_at, and deletes members.
"""
import sqlalchemy as sa

from alembic import op

revision = "0029_lti_roster"
down_revision = "0028_job_system_failure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lti_platforms", sa.Column("token_url", sa.String(), nullable=True))
    op.add_column("lti_contexts", sa.Column("memberships_url", sa.String(), nullable=True))
    op.add_column("lti_contexts", sa.Column("roster_synced_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("GRANT UPDATE (memberships_url) ON lti_contexts TO app_runtime")
    op.execute("GRANT SELECT ON lti_platforms, lti_contexts, lti_identities TO app_worker")
    op.execute("GRANT UPDATE (roster_synced_at) ON lti_contexts TO app_worker")
    op.execute("GRANT SELECT, DELETE ON course_members TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT, DELETE ON course_members FROM app_worker")
    op.execute("REVOKE UPDATE (roster_synced_at) ON lti_contexts FROM app_worker")
    op.execute("REVOKE SELECT ON lti_platforms, lti_contexts, lti_identities FROM app_worker")
    op.execute("REVOKE UPDATE (memberships_url) ON lti_contexts FROM app_runtime")
    op.drop_column("lti_contexts", "roster_synced_at")
    op.drop_column("lti_contexts", "memberships_url")
    op.drop_column("lti_platforms", "token_url")
