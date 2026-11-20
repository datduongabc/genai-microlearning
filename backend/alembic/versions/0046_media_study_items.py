"""Flashcard sets and videos as study packets, rated on criteria of their own.

study_items gains the groups 'flashcards' and 'video', the id of the set or video it was taken
from (one packet per source in a study) and, for a rendered video, the key of its MP4, which the
blind view carries so a rater can play it without reading the video row.
A media rating keeps its scores in ratings.scores; the eight module columns stay for modules and
are empty for media, never half filled.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0046_media_study_items"
down_revision = "0045_rater_pairs_explicit"
branch_labels = None
depends_on = None

MODULE_COLUMNS = ("accuracy", "completeness", "consistency", "hallucination_score",
                  "slide_quality", "quiz_quality", "bloom_fidelity", "query_alignment")
KEY = "COALESCE(pair_id, id)::text"


def _group_check(groups: str):
    op.drop_constraint("ck_study_items_group", "study_items")
    op.create_check_constraint("ck_study_items_group", "study_items", f'"group" IN ({groups})')


def upgrade():
    _group_check("'human', 'llm_no_rag', 'rag_llm', 'flashcards', 'video'")
    op.add_column("study_items", sa.Column("source_id", sa.UUID()))
    op.add_column("study_items", sa.Column("media_key", sa.Text()))
    op.create_unique_constraint("uq_study_items_source", "study_items", ["study_id", "source_id"])
    for column in MODULE_COLUMNS:
        op.alter_column("ratings", column, nullable=True)
    op.add_column("ratings", sa.Column("scores", JSONB))
    filled = f"num_nonnulls({', '.join(MODULE_COLUMNS)})"
    op.create_check_constraint("ck_ratings_shape", "ratings",
                               f"({filled} = 8 AND scores IS NULL) OR ({filled} = 0 AND scores IS NOT NULL)")
    op.execute(f"""
        CREATE OR REPLACE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet, reference_file_id, media_key FROM study_items
        WHERE study_item_assigned(study_id, {KEY})
    """)


def downgrade():
    op.execute("DELETE FROM study_items WHERE \"group\" IN ('flashcards', 'video')")
    op.execute("DROP VIEW blind_study_items")
    op.execute(f"""
        CREATE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet, reference_file_id FROM study_items
        WHERE study_item_assigned(study_id, {KEY})
    """)
    op.execute("GRANT SELECT ON blind_study_items TO app_runtime")
    op.drop_constraint("ck_ratings_shape", "ratings")
    op.drop_column("ratings", "scores")
    for column in MODULE_COLUMNS:
        op.alter_column("ratings", column, nullable=False)
    op.drop_constraint("uq_study_items_source", "study_items")
    op.drop_column("study_items", "media_key")
    op.drop_column("study_items", "source_id")
    _group_check("'human', 'llm_no_rag', 'rag_llm'")
