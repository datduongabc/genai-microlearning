"""Flashcard sets carry a draft version, as videos do.

A save sends the whole list of cards. Without a version, a tab holding an older copy saved over
the edits another tab had just saved, and nobody was told.
"""
import sqlalchemy as sa

from alembic import op

revision = "0040_flashcard_draft_version"
down_revision = "0039_lti_roster_checked_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flashcard_sets", sa.Column("draft_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("flashcard_sets", "draft_version")
