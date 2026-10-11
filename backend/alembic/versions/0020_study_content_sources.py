"""Researcher-only provenance of study component snapshots."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_study_content_sources"
down_revision = "0019_blind_ratings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("study_items", sa.Column("content_sources", postgresql.JSONB(),
                                          nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("study_items", "content_sources")
