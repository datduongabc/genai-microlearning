"""Moodle capability replaces app course claims and exclusive mappings."""
from alembic import op
import sqlalchemy as sa

revision = "0016_moodle_course_access"
down_revision = "0015_account_activation"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("courses_moodle_course_id_key", "courses", type_="unique")
    op.drop_table("moodle_course_claims")


def downgrade():
    op.create_table("moodle_course_claims",
        sa.Column("moodle_course_id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_moodle_course_claims_owner_id", "moodle_course_claims", ["owner_id"])
    op.execute("GRANT SELECT, INSERT ON moodle_course_claims TO app_runtime")
    op.create_unique_constraint("courses_moodle_course_id_key", "courses", ["moodle_course_id"])
