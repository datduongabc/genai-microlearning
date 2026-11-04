"""Per-stage timing of each module generation."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_generation_timings"
down_revision = "0022_extraction_warnings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("modules", sa.Column("generation_timings", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("modules", "generation_timings")
