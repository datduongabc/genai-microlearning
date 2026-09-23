"""Vai trò student là mã chết: mọi lần đăng ký đều gán cứng teacher, và ứng dụng này
chỉ dành cho giảng viên vì học viên học trên Moodle.

Bỏ một giá trị khỏi enum của PostgreSQL không làm trực tiếp được, phải dựng lại kiểu,
nên việc đó đáng có test riêng."""

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


def test_enum_vai_tro_chi_con_teacher(db_sau_migration):
    with db_sau_migration.connect() as conn:
        gia_tri = {
            r[0] for r in conn.execute(text(
                "select enumlabel from pg_enum e "
                "join pg_type t on t.oid = e.enumtypid where t.typname = 'userrole'"
            ))
        }

    assert gia_tri == {"teacher"}


def test_cot_role_van_dung_kieu_userrole(db_sau_migration):
    with db_sau_migration.connect() as conn:
        kieu = conn.execute(text(
            "select format_type(atttypid, atttypmod) from pg_attribute "
            "where attrelid = 'users'::regclass and attname = 'role'"
        )).scalar()

    assert kieu == "userrole"
