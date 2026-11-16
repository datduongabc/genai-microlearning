"""A retried flashcard or video-script job puts its set or video back to `generating`.

When such a job fails, its set or video is marked `failed`, and every guard on these items (delete,
a second generation with the same focus, editing a video's scenes) checks `status = 'generating'`.
Retrying only requeued the job, so while it ran again the item still read `failed`: it could be
deleted under the job, generated a second time, or have its scenes edited and then overwritten.

It stays the SECURITY DEFINER function from 0007, with the same checks and the same result: an
admin retries other lecturers' jobs, and row level security would hide their sets and videos from
an UPDATE run in the admin's own session.
"""
from alembic import op

revision = "0036_retry_media_jobs"
down_revision = "0035_flashcard_set_cost"
branch_labels = None
depends_on = None

_HEAD = (
    "CREATE OR REPLACE FUNCTION retry_failed_job(p_job_id uuid) RETURNS boolean LANGUAGE plpgsql "
    "SECURITY DEFINER SET search_path = public, pg_temp AS $$ "
    "DECLARE v_document uuid; v_type jobtype; v_payload jsonb; "
    "v_caller uuid := NULLIF(current_setting('app.current_user_id', true), '')::uuid; "
    "BEGIN "
    "SELECT f.document_id, f.job_type INTO v_document, v_type FROM open_failed_jobs f "
    "WHERE f.id = p_job_id AND (current_user_is_admin() OR f.owner_id = v_caller); "
    "IF NOT FOUND THEN RETURN false; END IF; "
    "UPDATE jobs SET status = 'queued', attempts = 0, run_after = now(), locked_at = NULL, "
    "locked_by = NULL WHERE id = p_job_id AND status = 'failed' RETURNING payload INTO v_payload; "
    "IF NOT FOUND THEN RETURN false; END IF; "
    "IF v_type = 'document_pipeline' THEN "
    "UPDATE documents SET status = 'processing' WHERE id = v_document AND status = 'failed'; "
)
# The ids come from the payload the API wrote; a job without one (none is made that way) only requeues.
_MEDIA = (
    "ELSIF v_type = 'flashcard_generation' AND v_payload ? 'flashcard_set_id' THEN "
    "UPDATE flashcard_sets SET status = 'generating' "
    "WHERE id = (v_payload->>'flashcard_set_id')::uuid AND status = 'failed'; "
    # The error is the failure being retried; the editor shows it while the video reads failed.
    "ELSIF v_type = 'video_script' AND v_payload ? 'video_id' THEN "
    "UPDATE videos SET status = 'generating', error = NULL "
    "WHERE id = (v_payload->>'video_id')::uuid AND status = 'failed'; "
)
_TAIL = "END IF; RETURN true; END $$"


def upgrade():
    op.execute(_HEAD + _MEDIA + _TAIL)


def downgrade():
    op.execute(_HEAD + _TAIL)
