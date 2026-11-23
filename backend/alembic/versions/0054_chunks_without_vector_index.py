"""Index the chunks that have no vector, for the workers' re-embedding pass.

Every worker looks for such chunks every 5 minutes (app/jobs/queue.py, _reembed_loop). Without an
index each look reads the whole table, about 3 KB of vector a chunk, even when there is nothing to do.
The index holds only chunks without a vector, so it is empty almost all the time.

Revision ID: 0054_chunks_without_vector_index
Revises: 0053_gemini_vectors
"""

from alembic import op

revision = "0054_chunks_without_vector_index"
down_revision = "0053_gemini_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_chunks_without_vector ON chunks (document_id, chunk_index) WHERE embedding IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_chunks_without_vector")
