"""Admin-only studies, anonymous packets and independent rubric ratings."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018_study_model"
down_revision = "0011_module_generation_models"
branch_labels = None
depends_on = None

SCORE_FIELDS = (
    "accuracy", "completeness", "consistency", "hallucination_score",
    "slide_quality", "quiz_quality", "bloom_fidelity", "query_alignment",
)


def _id():
    return sa.Column("id", sa.UUID(), primary_key=True)


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.add_column("modules", sa.Column("generation_payload", postgresql.JSONB()))
    op.add_column("modules", sa.Column("paired_module_id", sa.UUID(),
                                      sa.ForeignKey("modules.id", ondelete="CASCADE")))
    op.create_index("ix_modules_paired_module_id", "modules", ["paired_module_id"])
    op.execute("""
        UPDATE modules m SET generation_payload =
            '{"self_check":true,"output_language":"vi","custom_instructions":"","use_retrieval":true}'::jsonb
            || j.payload
        FROM (SELECT DISTINCT ON (module_id) module_id, payload
              FROM jobs WHERE job_type = 'module_generation' AND module_id IS NOT NULL
              ORDER BY module_id, created_at DESC, id) j
        WHERE m.id = j.module_id
    """)
    op.execute("""
        UPDATE modules m SET paired_module_id = source.id
        FROM modules source
        WHERE m.generation_payload->>'use_retrieval' = 'false'
          AND m.generation_payload->>'paired_module_id' = source.id::text
    """)
    op.create_table(
        "studies", _id(), sa.Column("name", sa.String(), nullable=False),
        sa.Column("rubric_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"), _created(),
        sa.CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_studies_status"),
    )
    op.create_table(
        "study_items", _id(),
        sa.Column("study_id", sa.UUID(), sa.ForeignKey("studies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group", sa.String(), nullable=False),
        sa.Column("anon_code", sa.String(), nullable=False),
        sa.Column("module_id", sa.UUID(), sa.ForeignKey("modules.id", ondelete="RESTRICT")),
        sa.Column("packet", postgresql.JSONB(), nullable=False), _created(),
        sa.CheckConstraint("\"group\" IN ('human', 'llm_no_rag', 'rag_llm')", name="ck_study_items_group"),
        sa.CheckConstraint("(\"group\" = 'rag_llm') = (module_id IS NOT NULL)", name="ck_study_items_module"),
        sa.UniqueConstraint("study_id", "anon_code", name="uq_study_items_code", deferrable=True, initially="DEFERRED"),
        sa.UniqueConstraint("study_id", "module_id", name="uq_study_items_module"),
    )
    op.create_index("ix_study_items_study_id", "study_items", ["study_id"])
    op.create_index("ix_study_items_module_id", "study_items", ["module_id"])
    op.create_table(
        "ratings", _id(),
        sa.Column("study_item_id", sa.UUID(), sa.ForeignKey("study_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("rater_kind", sa.String(), nullable=False),
        *(sa.Column(field, postgresql.JSONB(), nullable=False) for field in SCORE_FIELDS),
        sa.Column("overall_notes", sa.Text(), nullable=False),
        sa.Column("hallucination_findings", postgresql.JSONB(), nullable=False, server_default="[]"),
        _created(),
        sa.CheckConstraint("(rater_kind = 'human' AND user_id IS NOT NULL) OR "
                           "(rater_kind = 'llm_judge' AND user_id IS NULL)", name="ck_ratings_rater"),
        sa.UniqueConstraint("study_item_id", "user_id", name="uq_ratings_user"),
    )
    op.create_index("ix_ratings_study_item_id", "ratings", ["study_item_id"])
    op.create_index("ix_ratings_user_id", "ratings", ["user_id"])
    op.create_index("uq_ratings_llm_judge", "ratings", ["study_item_id"], unique=True,
                    postgresql_where=sa.text("rater_kind = 'llm_judge'"))
    for table in ("studies", "study_items", "ratings"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_runtime")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_admin ON {table} TO app_runtime "
                   "USING (current_user_is_admin()) WITH CHECK (current_user_is_admin())")

    op.execute("""
        CREATE FUNCTION modules_referenced_by_study(p_ids uuid[]) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (
            SELECT 1 FROM study_items i JOIN modules m ON m.id = i.module_id
            JOIN courses c ON c.id = m.course_id
            WHERE m.id = ANY(p_ids)
              AND c.owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        ) $$
    """)
    op.execute("REVOKE ALL ON FUNCTION modules_referenced_by_study(uuid[]) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION modules_referenced_by_study(uuid[]) TO app_runtime")


def downgrade():
    op.execute("DROP FUNCTION modules_referenced_by_study(uuid[])")
    op.drop_index("ix_modules_paired_module_id", table_name="modules")
    op.drop_column("modules", "paired_module_id")
    op.drop_column("modules", "generation_payload")
    for table in ("ratings", "study_items", "studies"):
        op.drop_table(table)
