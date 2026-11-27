"""The admin retries only what the system failed; a failure in the lecturer's input stays theirs.

The worker already tells the two apart (jobs.system_failure: out of tries on an outage, a rate limit or
something unexpected, not a ValueError about the input). The admin's view now carries it, and
retry_failed_job refuses an admin a job the system did not fail, so the rule holds whatever calls it.
The lecturer still retries any failure of their own.

Columns are only appended, as CREATE OR REPLACE VIEW requires.
"""
from alembic import op

revision = "0055_admin_retry_system_failures"
down_revision = "0054_chunks_without_vector_index"
branch_labels = None
depends_on = None

_OPEN = (
    "CREATE OR REPLACE VIEW open_failed_jobs AS "
    "SELECT j.id, j.job_type, j.step, j.attempts, j.max_attempts, j.last_error, j.created_at, "
    "j.module_id, j.document_id, d.filename AS document_filename, c.name AS course_name, "
    "c.owner_id, u.name AS owner_name, u.email AS owner_email{extra} "
    "FROM jobs j JOIN documents d ON d.id = j.document_id "
    "JOIN courses c ON c.id = d.course_id JOIN users u ON u.id = c.owner_id "
    "WHERE j.status = 'failed' "
    "AND NOT EXISTS (SELECT 1 FROM jobs n WHERE n.document_id = j.document_id "
    "AND n.job_type = j.job_type "
    "AND n.payload->>'flashcard_set_id' IS NOT DISTINCT FROM j.payload->>'flashcard_set_id' "
    "AND n.payload->>'video_id' IS NOT DISTINCT FROM j.payload->>'video_id' "
    "AND n.created_at > j.created_at)"
)
_ADMIN = (
    "CREATE OR REPLACE VIEW admin_failed_jobs AS "
    "SELECT id, job_type, step, attempts, max_attempts, last_error, created_at, module_id, "
    "document_filename, course_name, owner_name, owner_email{extra} "
    "FROM open_failed_jobs WHERE current_user_is_admin()"
)
# As in 0036, with only the WHERE of the first SELECT changed.
_RETRY = (
    "CREATE OR REPLACE FUNCTION retry_failed_job(p_job_id uuid) RETURNS boolean LANGUAGE plpgsql "
    "SECURITY DEFINER SET search_path = public, pg_temp AS $$ "
    "DECLARE v_document uuid; v_type jobtype; v_payload jsonb; "
    "v_caller uuid := NULLIF(current_setting('app.current_user_id', true), '')::uuid; "
    "BEGIN "
    "SELECT f.document_id, f.job_type INTO v_document, v_type FROM open_failed_jobs f "
    "WHERE f.id = p_job_id AND ({who}); "
    "IF NOT FOUND THEN RETURN false; END IF; "
    "UPDATE jobs SET status = 'queued', attempts = 0, run_after = now(), locked_at = NULL, "
    "locked_by = NULL WHERE id = p_job_id AND status = 'failed' RETURNING payload INTO v_payload; "
    "IF NOT FOUND THEN RETURN false; END IF; "
    "IF v_type = 'document_pipeline' THEN "
    "UPDATE documents SET status = 'processing' WHERE id = v_document AND status = 'failed'; "
    "ELSIF v_type = 'flashcard_generation' AND v_payload ? 'flashcard_set_id' THEN "
    "UPDATE flashcard_sets SET status = 'generating' "
    "WHERE id = (v_payload->>'flashcard_set_id')::uuid AND status = 'failed'; "
    "ELSIF v_type = 'video_script' AND v_payload ? 'video_id' THEN "
    "UPDATE videos SET status = 'generating', error = NULL "
    "WHERE id = (v_payload->>'video_id')::uuid AND status = 'failed'; "
    "END IF; RETURN true; END $$"
)


def upgrade():
    op.execute(_OPEN.format(extra=", j.system_failure"))
    op.execute(_ADMIN.format(extra=", system_failure"))
    op.execute(_RETRY.format(who="(current_user_is_admin() AND f.system_failure) OR f.owner_id = v_caller"))


def downgrade():
    op.execute(_RETRY.format(who="current_user_is_admin() OR f.owner_id = v_caller"))
    # A view cannot lose a column in place.
    op.execute("DROP VIEW admin_failed_jobs")
    op.execute("DROP VIEW open_failed_jobs")
    op.execute(_OPEN.format(extra=""))
    op.execute(_ADMIN.format(extra=""))
    op.execute("GRANT SELECT ON admin_failed_jobs TO app_runtime")
