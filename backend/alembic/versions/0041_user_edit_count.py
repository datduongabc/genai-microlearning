"""Changes asked for in words are counted a day per account, as pictures and renders are.

Each one is a paid model call, and only a per-minute rate limit held them before.
"""
import sqlalchemy as sa

from alembic import op

revision = "0041_user_edit_count"
down_revision = "0040_flashcard_draft_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("edit_day", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("edit_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "edit_count")
    op.drop_column("users", "edit_day")
