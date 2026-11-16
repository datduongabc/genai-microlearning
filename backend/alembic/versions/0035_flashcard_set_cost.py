"""What a flashcard set's text-model calls cost, and which models served them.

Videos have kept both since 0032; flashcard sets did not, so the lecturer could not see what a set
cost. Existing sets get the empty defaults: their calls were never priced and cannot be recovered.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0035_flashcard_set_cost"
down_revision = "0034_video_rendered_scenes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("flashcard_sets", sa.Column("cost", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("flashcard_sets", sa.Column("served_models", postgresql.JSONB(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("flashcard_sets", "served_models")
    op.drop_column("flashcard_sets", "cost")
