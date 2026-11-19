"""A failure stays open until that same set or video is made again, not any set or video.

One document can have many flashcard sets and videos. The old rule closed set A's failure as soon
as set B, on the same document, had a newer job, so the admin list lost a failure the lecturer
still saw on the course page.
"""
from alembic import op

revision = "0042_failures_per_item"
down_revision = "0041_user_edit_count"
branch_labels = None
depends_on = None


def _view(per_item: bool):
    item = ("AND n.payload->>'flashcard_set_id' IS NOT DISTINCT FROM j.payload->>'flashcard_set_id' "
            "AND n.payload->>'video_id' IS NOT DISTINCT FROM j.payload->>'video_id' ") if per_item else ""
    # Same projected columns: the admin view, its grants and the retry function depend on them.
    op.execute(
        "CREATE OR REPLACE VIEW open_failed_jobs AS "
        "SELECT j.id, j.job_type, j.step, j.attempts, j.max_attempts, j.last_error, j.created_at, "
        "j.module_id, j.document_id, d.filename AS document_filename, c.name AS course_name, "
        "c.owner_id, u.name AS owner_name, u.email AS owner_email "
        "FROM jobs j JOIN documents d ON d.id = j.document_id "
        "JOIN courses c ON c.id = d.course_id JOIN users u ON u.id = c.owner_id "
        "WHERE j.status = 'failed' "
        "AND NOT EXISTS (SELECT 1 FROM jobs n WHERE n.document_id = j.document_id "
        f"AND n.job_type = j.job_type {item}AND n.created_at > j.created_at)"
    )


def upgrade():
    _view(per_item=True)


def downgrade():
    _view(per_item=False)
