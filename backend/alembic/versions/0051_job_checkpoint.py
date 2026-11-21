"""What a module generation job has already paid for, so that running it again does not pay twice.

A module makes 14 to 25 model calls in sequence. When its worker stopped part way (scaling in, a
deployment, a crash) or a step failed, the job ran again from the planner, and every call that had
succeeded was paid for again. Each finished step now writes its result here; a run reads it and
starts at the first step not done. It is the job's, not the module's: the module is written only
once everything is done, in one transaction.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0051_job_checkpoint"
down_revision = "0050_course_transfer"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("checkpoint", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("jobs", "checkpoint")
