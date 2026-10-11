"""Persist page extraction warnings independently of finished jobs."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_extraction_warnings"
down_revision = "0019_blind_ratings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("extraction_warnings", postgresql.JSONB(),
                                        nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("documents", "extraction_warnings")
