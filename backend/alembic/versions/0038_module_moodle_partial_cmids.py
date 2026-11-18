"""Modules remember the Moodle activities of a publish that failed part way.

The retry then creates only the missing ones. Before, it created a second Book and the lecturer
had to delete the first by hand.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0038_module_moodle_partial_cmids"
down_revision = "0037_module_moodle_published_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("modules", sa.Column("moodle_partial_cmids", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("modules", "moodle_partial_cmids")
