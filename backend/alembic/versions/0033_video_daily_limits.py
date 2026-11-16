"""Per-account UTC daily counters of the video pictures drawn and the videos rendered.

On the account row, as the generation (0017) and learner chat (0027) counters are: the request that
counts locks that row, so two at once cannot both take the last one, and the counters need no
table, policy or grant of their own.
"""
import sqlalchemy as sa

from alembic import op

revision = "0033_video_daily_limits"
down_revision = "0032_videos_flashcards"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("video_redraw_day", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("video_redraw_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("video_render_day", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("video_render_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("users", "video_render_count")
    op.drop_column("users", "video_render_day")
    op.drop_column("users", "video_redraw_count")
    op.drop_column("users", "video_redraw_day")
