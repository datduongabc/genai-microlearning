"""Keep one rating per judge family, allowing two independent judges per item."""
import sqlalchemy as sa

from alembic import op

revision = "0022_cross_family_judges"
down_revision = "0021_remove_textbook_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ratings", sa.Column("judge_family", sa.String()))
    op.execute("""UPDATE ratings SET judge_family = CASE
        WHEN judge_model ILIKE '%gpt%' THEN 'openai'
        WHEN judge_model ILIKE '%claude%' THEN 'anthropic'
        WHEN judge_model ILIKE '%gemini%' THEN 'google'
        ELSE 'legacy' END WHERE rater_kind = 'llm_judge'""")
    op.drop_index("uq_ratings_llm_judge", table_name="ratings")
    op.create_index("uq_ratings_llm_judge", "ratings", ["study_item_id", "judge_family"],
                    unique=True, postgresql_where=sa.text("rater_kind = 'llm_judge'"))
    op.create_check_constraint("ck_ratings_judge_family", "ratings",
                               "rater_kind != 'llm_judge' OR judge_family IS NOT NULL")


def downgrade():
    # Never silently discard a second judge's research data to downgrade.
    op.drop_constraint("ck_ratings_judge_family", "ratings")
    op.drop_index("uq_ratings_llm_judge", table_name="ratings")
    op.create_index("uq_ratings_llm_judge", "ratings", ["study_item_id"], unique=True,
                    postgresql_where=sa.text("rater_kind = 'llm_judge'"))
    op.drop_column("ratings", "judge_family")
