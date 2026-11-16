"""Migration 0036 can be undone, and redone: the downgrade is 0007's retry function, with no media branch."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import settings


def test_0036_downgrades_to_the_function_without_the_media_branches_and_upgrades_again():
    name = f"retry_media_migration_{uuid.uuid4().hex}"
    root = settings.database_url.rsplit("/", 1)[0]
    admin = create_engine(f"{root}/postgres", isolation_level="AUTOCOMMIT")
    engine = create_engine(f"{root}/{name}")

    def migrate(target, command="upgrade"):
        result = subprocess.run([sys.executable, "-m", "alembic", command, target],
                                cwd=Path(__file__).resolve().parents[2], capture_output=True,
                                text=True, timeout=120, check=False,
                                env={**os.environ, "DATABASE_URL": f"{root}/{name}"})
        assert result.returncode == 0, result.stderr

    def retry_function():
        with engine.connect() as conn:
            return conn.scalar(text("SELECT pg_get_functiondef('retry_failed_job(uuid)'::regprocedure)"))

    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    try:
        migrate("0036_retry_media_jobs")
        assert "flashcard_sets" in retry_function() and "videos" in retry_function()
        migrate("0035_flashcard_set_cost", "downgrade")
        assert "flashcard_sets" not in retry_function() and "documents" in retry_function()
        migrate("0036_retry_media_jobs")
        assert "flashcard_sets" in retry_function()
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name}"))
        admin.dispose()
