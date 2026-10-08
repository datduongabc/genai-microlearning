"""Persist actual served models without guessing historical alias mappings."""

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0011_module_generation_models"
down_revision = "0010_admin_course_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("modules", Column("generation_models", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("modules", "generation_models")
