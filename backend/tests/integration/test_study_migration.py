"""Exercise upgrade/downgrade on a fresh database, including runtime grants and RLS."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.config import settings


def test_study_migration_round_trip():
    name = f"study_migration_{uuid.uuid4().hex}"
    root = settings.database_url.rsplit("/", 1)[0]
    admin = create_engine(f"{root}/postgres", isolation_level="AUTOCOMMIT")
    backend = Path(__file__).resolve().parents[2]
    engine = create_engine(f"{root}/{name}")

    def migrate(target, command="upgrade"):
        result = subprocess.run([sys.executable, "-m", "alembic", command, target], cwd=backend,
                                capture_output=True, text=True, timeout=120, check=False,
                                env={**os.environ, "DATABASE_URL": f"{root}/{name}"})
        assert result.returncode == 0, result.stderr

    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    try:
        migrate("0011_module_generation_models")
        previous = set(inspect(engine).get_table_names())
        migrate("head")
        assert set(inspect(engine).get_table_names()) - previous == {"studies", "study_items", "ratings"}
        with engine.connect() as conn:
            for table in ("studies", "study_items", "ratings"):
                assert conn.scalar(text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table)"), {"table": table})
                assert conn.scalar(text("SELECT has_table_privilege('app_runtime', :table, 'SELECT,INSERT,UPDATE,DELETE')"), {"table": table})
                assert not conn.scalar(text("SELECT has_table_privilege('app_worker', :table, 'SELECT')"), {"table": table})
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0018_study_model"
        migrate("0011_module_generation_models", "downgrade")
        assert set(inspect(engine).get_table_names()) == previous
        migrate("head")
        assert {"studies", "study_items", "ratings"}.issubset(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name}"))
        admin.dispose()
