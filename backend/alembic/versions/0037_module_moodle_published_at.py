"""Modules remember when they were last published to Moodle.

Publishing again creates a second Book and two more quizzes in the Moodle course. The page only
knew it had published while it stayed open, so a reload or a second tab published again unasked.
"""
import sqlalchemy as sa

from alembic import op

revision = "0037_module_moodle_published_at"
down_revision = "0036_retry_media_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("modules", sa.Column("moodle_published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("modules", "moodle_published_at")
