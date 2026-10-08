import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def db_sau_migration():
    """Một cơ sở dữ liệu trắng đã chạy toàn bộ migration."""
    from app.config import settings

    ten_db = "test_role_enum"
    goc = settings.database_url.rsplit("/", 1)[0]
    quan_tri = create_engine(f"{goc}/postgres", isolation_level="AUTOCOMMIT")
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
        conn.execute(text(f"CREATE DATABASE {ten_db}"))

    url = f"{goc}/{ten_db}"
    ket_qua = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, check=False, capture_output=True, text=True, timeout=300,
        env={**os.environ, "DATABASE_URL": url},
    )
    if ket_qua.returncode != 0:
        pytest.fail(f"alembic upgrade head thất bại:\n{ket_qua.stderr[-3000:]}")
    engine = create_engine(url)

    yield engine

    engine.dispose()
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
    quan_tri.dispose()


def test_local_accounts_have_a_boolean_admin_flag_and_soft_delete(db_sau_migration):
    with db_sau_migration.connect() as conn:
        columns = dict(conn.execute(text("select attname, format_type(atttypid, atttypmod) "
                                        "from pg_attribute where attrelid = 'users'::regclass "
                                        "and attnum > 0 and not attisdropped")).all())
        assert columns['is_admin'] == 'boolean'
        assert columns['deleted_at'] == 'timestamp with time zone'
        assert 'role' not in columns
        assert not conn.execute(text("select 1 from pg_type where typname='userrole'")).first()
