"""A version on each part of a module, so a save made from an older copy is refused.

Two teachers of a course, or two tabs, editing the same part: the later save wrote over the earlier one without
a word. Every write (a manual edit, a question edit, an edit asked of the model) moves it on, and a save names
the version it was made from, as a video's or a flashcard set's draft_version does (0040).

Revision ID: 0056_module_component_version
Revises: 0055_admin_retry_system_failures
"""

import sqlalchemy as sa

from alembic import op

revision = "0056_module_component_version"
down_revision = "0055_admin_retry_system_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("module_components", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("module_components", "version")
