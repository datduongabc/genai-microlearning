"""Clear every chunk vector: the embedding model changes from the self-hosted Qwen3-Embedding to
Gemini Embedding 001 through the gateway (devops/gateway/config.yaml).

Revision ID: 0053_gemini_vectors
Revises: 0052_rubric_v3
"""

from alembic import op

revision = "0053_gemini_vectors"
down_revision = "0052_rubric_v3"
branch_labels = None
depends_on = None

# Same reason as 0005: vectors of two models share the 768 numbers but not the space, so a question
# embedded by Gemini against Qwen3 chunks ranks at random with no error. Cleared chunks are found by
# BM25 alone until scripts/reembed_chunks.py embeds them again; their text is kept.


def upgrade() -> None:
    op.execute("UPDATE chunks SET embedding = NULL WHERE embedding IS NOT NULL")


def downgrade() -> None:
    # Going back is a model change too: the vectors present are Gemini's.
    op.execute("UPDATE chunks SET embedding = NULL WHERE embedding IS NOT NULL")
