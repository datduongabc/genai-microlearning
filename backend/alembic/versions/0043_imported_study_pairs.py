"""A RAG packet may be loaded from its exported file, without the module it was made from.

The 2026-11-07 packets were exported from a database that no longer exists; the claim
measurements were made on those exact files, so the study rates them rather than new ones.
Only a RAG packet may still point at a module.
"""
from alembic import op

revision = "0043_imported_study_pairs"
down_revision = "0042_failures_per_item"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_study_items_module", "study_items", type_="check")
    op.create_check_constraint("ck_study_items_module", "study_items",
                               "module_id IS NULL OR \"group\" = 'rag_llm'")


def downgrade():
    op.drop_constraint("ck_study_items_module", "study_items", type_="check")
    op.create_check_constraint("ck_study_items_module", "study_items",
                               "(\"group\" = 'rag_llm') = (module_id IS NOT NULL)")
