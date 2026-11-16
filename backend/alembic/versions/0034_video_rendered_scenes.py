"""The scenes of a video as they were rendered: title, where each starts, and what it cites.

Learners see the render, not the draft. The draft can be edited after the render, and its scenes then
no longer match the file they are watching, so the render keeps its own list.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034_video_rendered_scenes"
down_revision = "0033_video_daily_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("rendered_scenes", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("videos", "rendered_scenes")
