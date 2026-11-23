"""Embeds again the chunks that have no vector: after the embedding model or the size of the
vectors changed (a migration clears the old ones, as 0005 did), or after a job stopped half way.
It works in batches and commits each one, and leaves a chunk that has a vector alone, so it can be
stopped and started again. The job workers run it on their own (app/jobs/queue.py, _reembed_loop), so
after such a migration nothing needs to be run by hand."""

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
            # Several workers run this at once (app/jobs/queue.py): each takes its own batch.
            .with_for_update(skip_locked=True)
            .all()
        )
        if not rows:
            return done
        for row, vector in zip(rows, embed_texts([row.content for row in rows]), strict=True):
            row.embedding = vector
        db.commit()
        done += len(rows)
