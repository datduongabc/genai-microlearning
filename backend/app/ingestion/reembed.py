"""Embeds again the chunks that have no vector: after the embedding provider or the size of the
vectors changed (migration d1f6a3c8e5b2 clears the old ones), or after a job stopped half way.
It works in batches and commits each one, and leaves a chunk that has a vector alone, so it can be
stopped and started again."""

from sqlalchemy.orm import Session

from app.ingestion.embeddings import BATCH_SIZE, embed_texts
from app.models import Chunk


def reembed_missing(db: Session, batch_size: int = BATCH_SIZE) -> int:
    """Returns how many chunks got a vector."""
    done = 0
    while True:
        rows = (
            db.query(Chunk)
            .filter(Chunk.embedding.is_(None))
            .order_by(Chunk.document_id, Chunk.chunk_index)
            .limit(batch_size)
            .all()
        )
        if not rows:
            return done
        for row, vector in zip(rows, embed_texts([row.content for row in rows]), strict=True):
            row.embedding = vector
        db.commit()
        done += len(rows)
