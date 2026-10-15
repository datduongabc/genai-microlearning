"""Queued study judges, snapshot provenance and judge provenance on ratings."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0020_study_judge"
down_revision = "0019_blind_ratings"
branch_labels = None
depends_on = None


# Preserve the human invitation, closed-study and immutable-identity guards.
GUARD = """
        CREATE OR REPLACE FUNCTION guard_rating_write() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE sid uuid; study_state text;
        BEGIN
            IF TG_OP = 'UPDATE' AND (NEW.study_item_id IS DISTINCT FROM OLD.study_item_id
                OR NEW.user_id IS DISTINCT FROM OLD.user_id
                OR NEW.rater_kind IS DISTINCT FROM OLD.rater_kind) THEN
                RAISE EXCEPTION 'Rating identity cannot change' USING ERRCODE = '23514';
            END IF;
            SELECT study_id INTO sid FROM study_items WHERE id = NEW.study_item_id;
            SELECT status INTO study_state FROM studies WHERE id = sid FOR UPDATE;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = session_user AND rolsuper)
                AND current_user_is_admin() IS NOT TRUE
                AND rating_item_invited(NEW.study_item_id) IS NOT TRUE{worker_exception} THEN
                RAISE EXCEPTION 'Study not found' USING ERRCODE = '42501';
            END IF;
            IF study_state = 'closed' THEN
                RAISE EXCEPTION 'Study is closed' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
"""


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
    op.execute(GUARD.format(worker_exception="\n                AND NOT (session_user = 'app_worker' AND NEW.rater_kind = 'llm_judge')"))


def downgrade():
    op.execute(GUARD.format(worker_exception=""))
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
