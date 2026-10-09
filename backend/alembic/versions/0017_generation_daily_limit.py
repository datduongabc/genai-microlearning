"""Durable per-account UTC daily module generation counters."""
import sqlalchemy as sa

from alembic import op

revision = "0017_generation_daily_limit"
down_revision = "0016_moodle_course_access"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("generation_day", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("generation_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("users", "generation_count")
    op.drop_column("users", "generation_day")
