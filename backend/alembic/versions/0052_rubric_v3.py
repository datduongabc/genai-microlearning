"""Rubric v3 (docs/rubric.md): examples and explanations as a ninth criterion, and the item-writing
flaws of each question, from which the quiz score is computed.

Ratings made under an older rubric keep their eight columns and leave both new ones empty; a study
records its rubric and takes new module ratings only on the current one.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0052_rubric_v3"
down_revision = "0051_job_checkpoint"
branch_labels = None
depends_on = None

FILLED = ("num_nonnulls(accuracy, completeness, consistency, hallucination_score, slide_quality, quiz_quality, "
          "bloom_fidelity, query_alignment)")


def upgrade():
    op.add_column("ratings", sa.Column("examples_feedback", JSONB))
    op.add_column("ratings", sa.Column("quiz_flaws", JSONB))
    op.drop_constraint("ck_ratings_shape", "ratings")
    op.create_check_constraint(
        "ck_ratings_shape", "ratings",
        f"({FILLED} = 8 AND scores IS NULL AND num_nonnulls(examples_feedback, quiz_flaws) IN (0, 2)) OR "
        f"({FILLED} = 0 AND num_nonnulls(examples_feedback, quiz_flaws) = 0 AND scores IS NOT NULL)")


def downgrade():
    op.drop_constraint("ck_ratings_shape", "ratings")
    op.create_check_constraint("ck_ratings_shape", "ratings",
                               f"({FILLED} = 8 AND scores IS NULL) OR ({FILLED} = 0 AND scores IS NOT NULL)")
    op.drop_column("ratings", "quiz_flaws")
    op.drop_column("ratings", "examples_feedback")
