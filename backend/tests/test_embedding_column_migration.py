"""Moving a database that holds vectors of the old size to the size the current embedding provider
gives. A vector of one size cannot be compared with one of another, so the old ones are cleared (the
chunks and their text stay, and can be embedded again), and the column takes the new size."""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.models.models import EMBEDDING_DIM

BACKEND = Path(__file__).resolve().parent.parent
# The last revision that has the old vector size.
BEFORE = "c8e4a1f6b3d9"
OLD_DIM = 384


def _alembic(*args: str) -> None:
    # A separate process: alembic reconfigures logging for the whole interpreter.
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, check=True, capture_output=True, timeout=180
    )


def _vector(dim: int) -> str:
    return "[" + ",".join(["0.1"] * dim) + "]"


@pytest.fixture
def old_size_with_a_chunk():
    """The database as it was: the old column size, and a chunk that holds a vector of that size."""
    _alembic("downgrade", BEFORE)
    ids = {name: uuid.uuid4() for name in ("user", "course", "document", "chunk")}
    db = SessionLocal()
    try:
        db.execute(
            text("insert into users (id, email, name, hashed_password, role) values (:u, :e, 'M', 'x', 'teacher')"),
            {"u": ids["user"], "e": f"vec-{uuid.uuid4().hex[:8]}@example.com"},
        )
        db.execute(
            text("insert into courses (id, name, owner_id) values (:c, 'Vectors', :u)"),
            {"c": ids["course"], "u": ids["user"]},
        )
        db.execute(
            text("insert into documents (id, course_id, filename, status, kind) values (:d, :c, 'a.pdf', 'embedded', 'lecture')"),
            {"d": ids["document"], "c": ids["course"]},
        )
        db.execute(
            text(
                "insert into chunks (id, document_id, course_id, chunk_index, content, embedding) "
                "values (:k, :d, :c, 0, 'nội dung của chunk', CAST(:v AS vector(384)))"
            ),
            {"k": ids["chunk"], "d": ids["document"], "c": ids["course"], "v": _vector(OLD_DIM)},
        )
        db.commit()
    finally:
        db.close()

    yield ids

    _alembic("upgrade", "head")
    db = SessionLocal()
    try:
        db.execute(text("delete from courses where id = :c"), {"c": ids["course"]})
        db.execute(text("delete from users where id = :u"), {"u": ids["user"]})
        db.commit()
    finally:
        db.close()


def _column_type() -> str:
    db = SessionLocal()
    try:
        return db.execute(
            text(
                "select format_type(a.atttypid, a.atttypmod) from pg_attribute a "
                "where a.attrelid = 'chunks'::regclass and a.attname = 'embedding'"
            )
        ).scalar_one()
    finally:
        db.close()


def _chunk(chunk_id):
    db = SessionLocal()
    try:
        return db.execute(text("select content, embedding is null from chunks where id = :k"), {"k": chunk_id}).one()
    finally:
        db.close()


def test_the_column_is_the_size_of_the_embedding_provider_after_the_move(old_size_with_a_chunk):
    assert _column_type() == f"vector({OLD_DIM})"

    _alembic("upgrade", "head")

    assert _column_type() == f"vector({EMBEDDING_DIM})"


def test_the_chunk_keeps_its_text_and_loses_only_the_vector_that_no_longer_fits(old_size_with_a_chunk):
    _alembic("upgrade", "head")

    content, vector_is_null = _chunk(old_size_with_a_chunk["chunk"])

    assert content == "nội dung của chunk"
    assert vector_is_null


def test_a_vector_of_the_new_size_can_be_stored_after_the_move(old_size_with_a_chunk):
    _alembic("upgrade", "head")

    db = SessionLocal()
    try:
        db.execute(
            text("update chunks set embedding = CAST(:v AS vector(768)) where id = :k"),
            {"v": _vector(EMBEDDING_DIM), "k": old_size_with_a_chunk["chunk"]},
        )
        db.commit()
    finally:
        db.close()

    assert _chunk(old_size_with_a_chunk["chunk"])[1] is False


def test_moving_back_and_forth_again_is_harmless(old_size_with_a_chunk):
    _alembic("upgrade", "head")
    _alembic("downgrade", BEFORE)
    _alembic("upgrade", "head")

    assert _column_type() == f"vector({EMBEDDING_DIM})"
    assert _chunk(old_size_with_a_chunk["chunk"])[0] == "nội dung của chunk"
