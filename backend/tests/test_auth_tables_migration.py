"""Moving a database that already holds the old authentication tables to the current schema.
The account-lockout and event tables go away, the refresh-token table loses its session
bookkeeping, and a login that is still valid keeps working across the move."""

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.security import generate_refresh_token, hash_refresh_token
from app.db import SessionLocal
from app.main import app

BACKEND = Path(__file__).resolve().parent.parent
# The last revision that still has the old authentication tables.
BEFORE = "a4d7e2c9b6f1"

client = TestClient(app)


def _alembic(*args: str) -> None:
    # A separate process: alembic reconfigures logging for the whole interpreter, which would
    # silence the loggers other tests listen to.
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, check=True, capture_output=True, timeout=180
    )


@pytest.fixture
def old_schema_with_data():
    """The database as it was before: old tables present, holding a user, a still-valid
    refresh token, a lockout counter and an event. Restored to the current schema after."""
    _alembic("downgrade", BEFORE)
    email = f"migrate-{uuid.uuid4().hex[:10]}@example.com"
    user_id = uuid.uuid4()
    raw_token = generate_refresh_token()
    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        db.execute(
            text("insert into users (id, email, name, hashed_password, role) values (:i, :e, 'M', 'x', 'teacher')"),
            {"i": user_id, "e": email},
        )
        db.execute(
            text(
                "insert into refresh_tokens (id, user_id, token_hash, expires_at, family_id, session_expires_at) "
                "values (gen_random_uuid(), :u, :h, :exp, gen_random_uuid(), :end)"
            ),
            {"u": user_id, "h": hash_refresh_token(raw_token), "exp": now + timedelta(days=3), "end": now + timedelta(days=20)},
        )
        db.execute(
            text("insert into login_throttles (email, failures, last_failure_at) values (:e, 2, now())"), {"e": email}
        )
        db.execute(
            text("insert into auth_events (id, created_at, event, email) values (gen_random_uuid(), now(), 'x', :e)"),
            {"e": email},
        )
        db.commit()
    finally:
        db.close()

    yield raw_token

    _alembic("upgrade", "head")
    db = SessionLocal()
    try:
        db.execute(text("delete from refresh_tokens where user_id = :u"), {"u": user_id})
        db.execute(text("delete from users where id = :u"), {"u": user_id})
        db.commit()
    finally:
        db.close()


def _exists(table: str) -> bool:
    db = SessionLocal()
    try:
        return db.execute(text("select to_regclass(:t)"), {"t": table}).scalar_one() is not None
    finally:
        db.close()


def _columns(table: str) -> set[str]:
    db = SessionLocal()
    try:
        rows = db.execute(text("select column_name from information_schema.columns where table_name = :t"), {"t": table})
        return {row[0] for row in rows}
    finally:
        db.close()


def test_the_lockout_and_event_tables_are_gone_after_the_move(old_schema_with_data):
    assert _exists("login_throttles") and _exists("auth_events")

    _alembic("upgrade", "head")

    assert not _exists("login_throttles")
    assert not _exists("auth_events")


def test_the_refresh_token_table_loses_only_its_session_bookkeeping(old_schema_with_data):
    _alembic("upgrade", "head")

    columns = _columns("refresh_tokens")

    assert not columns & {"family_id", "session_expires_at", "revoked_reason"}
    assert {"user_id", "token_hash", "expires_at", "revoked_at"} <= columns


def test_a_login_that_is_still_valid_keeps_working_across_the_move(old_schema_with_data):
    _alembic("upgrade", "head")

    assert client.post("/auth/refresh", json={"refresh_token": old_schema_with_data}).status_code == 200


def test_moving_back_and_forth_again_is_harmless(old_schema_with_data):
    _alembic("upgrade", "head")
    _alembic("downgrade", BEFORE)
    _alembic("upgrade", "head")

    assert not _exists("auth_events")
