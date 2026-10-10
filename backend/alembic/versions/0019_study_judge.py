"""Queued study judges, snapshot provenance and judge provenance on ratings."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0019_study_judge"
down_revision = "0018_study_model"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE jobtype ADD VALUE IF NOT EXISTS 'study_judge'")
    op.add_column("study_items", sa.Column("generator_model", sa.String()))
    op.add_column("study_items", sa.Column("generation_models", JSONB()))
    op.add_column("ratings", sa.Column("judge_alias", sa.String()))
    op.add_column("ratings", sa.Column("judge_model", sa.String()))
    op.add_column("jobs", sa.Column("study_id", sa.UUID(), sa.ForeignKey("studies.id", ondelete="CASCADE")))
    op.create_index("ix_jobs_study_id", "jobs", ["study_id"])
    op.execute("CREATE POLICY jobs_study_admin ON jobs TO app_runtime "
               "USING (job_type = 'study_judge' AND study_id IS NOT NULL AND current_user_is_admin()) "
               "WITH CHECK (job_type = 'study_judge' AND study_id IS NOT NULL AND current_user_is_admin())")
    op.execute("GRANT SELECT ON studies, study_items TO app_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ratings TO app_worker")


def downgrade():
    op.execute("DELETE FROM jobs WHERE job_type = 'study_judge'")
    op.execute("DROP POLICY jobs_study_admin ON jobs")
    op.execute("REVOKE SELECT ON studies, study_items FROM app_worker")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON ratings FROM app_worker")
    op.drop_index("ix_jobs_study_id", table_name="jobs")
    op.drop_column("jobs", "study_id")
    op.drop_column("ratings", "judge_model")
    op.drop_column("ratings", "judge_alias")
    op.drop_column("study_items", "generation_models")
    op.drop_column("study_items", "generator_model")
    # PostgreSQL enum values cannot be removed; the unused value remains on downgrade.
