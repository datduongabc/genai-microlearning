"""LTI 1.3 launch: platforms, deployments, identities, contexts, login states and launch codes."""
import sqlalchemy as sa

from alembic import op

revision = "0026_lti"
down_revision = "0025_worker_rls_policies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lti_platforms",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("auth_login_url", sa.String(), nullable=False),
        sa.Column("jwks_url", sa.String(), nullable=False),
        sa.Column("links_moodle", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("issuer", "client_id"),
    )
    op.create_table(
        "lti_deployments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("platform_id", sa.UUID(), sa.ForeignKey("lti_platforms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.String(), nullable=False),
        sa.UniqueConstraint("platform_id", "deployment_id"),
    )
    op.create_table(
        "lti_identities",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("platform_id", sa.UUID(), sa.ForeignKey("lti_platforms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sub", sa.String(), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("platform_id", "sub"),
    )
    op.create_index("ix_lti_identities_user_id", "lti_identities", ["user_id"])
    op.create_table(
        "lti_contexts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("platform_id", sa.UUID(), sa.ForeignKey("lti_platforms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.String(), nullable=False),
        sa.Column("context_id", sa.String(), nullable=False),
        sa.Column("course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("platform_id", "deployment_id", "context_id"),
    )
    op.create_index("ix_lti_contexts_course_id", "lti_contexts", ["course_id"])
    op.create_table(
        "lti_login_states",
        sa.Column("state_hash", sa.String(), primary_key=True),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("platform_id", sa.UUID(), sa.ForeignKey("lti_platforms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lti_login_states_expires_at", "lti_login_states", ["expires_at"])
    op.create_index("ix_lti_login_states_platform_id", "lti_login_states", ["platform_id"])
    op.create_table(
        "lti_launch_codes",
        sa.Column("code_hash", sa.String(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lti_launch_codes_expires_at", "lti_launch_codes", ["expires_at"])
    # For the cascades from users and courses.
    op.create_index("ix_lti_launch_codes_user_id", "lti_launch_codes", ["user_id"])
    op.create_index("ix_lti_launch_codes_course_id", "lti_launch_codes", ["course_id"])

    # A launch comes before any sign-in, so these are read and written by the request role.
    # Platforms and deployments are configuration: the requests only read them.
    op.execute("GRANT SELECT ON lti_platforms, lti_deployments TO app_runtime")
    op.execute("GRANT SELECT, INSERT ON lti_identities, lti_contexts TO app_runtime")
    op.execute("GRANT SELECT, INSERT, DELETE ON lti_login_states, lti_launch_codes TO app_runtime")
    # The periodic clean-up removes what was never used. A DELETE with a WHERE needs SELECT on
    # the column it filters by, and only that one.
    for table in ("lti_login_states", "lti_launch_codes"):
        op.execute(f"GRANT DELETE ON {table} TO app_worker")
        op.execute(f"GRANT SELECT (expires_at) ON {table} TO app_worker")


def downgrade():
    for table in ("lti_launch_codes", "lti_login_states", "lti_contexts", "lti_identities",
                  "lti_deployments", "lti_platforms"):
        op.drop_table(table)
