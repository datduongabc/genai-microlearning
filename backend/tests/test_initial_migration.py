"""Chạy migration lên một cơ sở dữ liệu trắng phải dựng lại đúng schema mà các mô hình khai báo,
kèm cả vai trò và Row Level Security.

Alembic autogenerate không sinh ra vai trò, quyền hay policy RLS. Nếu chúng bị bỏ quên thì
cách ly dữ liệu giữa các giảng viên biến mất mà không có lỗi nào báo ra, nên phải kiểm tra
tường minh ở đây."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.models  # noqa: F401  (đăng ký mọi bảng lên Base.metadata)
from app.db import Base

BACKEND = Path(__file__).resolve().parents[1]

# Chuoi lan cua RLS: moi bang loc theo cha truc tiep, cha cung dang bi loc nen
# bo loc lan len tan courses. Gia tri la cac manh bat buoc phai co trong dieu kien.
CHUOI_LAN = {
    "courses": ["owner_id", "app.current_user_id"],
    "chat_sessions": ["course_id", "FROM courses"],
    "chunks": ["course_id", "FROM courses"],
    "documents": ["course_id", "FROM courses"],
    "modules": ["course_id", "FROM courses"],
    "document_sections": ["document_id", "FROM documents"],
    "evaluations": ["module_id", "FROM modules"],
    "module_components": ["module_id", "FROM modules"],
    "questions": ["component_id", "FROM module_components"],
    "chat_messages": ["session_id", "FROM chat_sessions"],
    "jobs": ["document_id", "module_id"],
}
CAC_BANG_CO_RLS = set(CHUOI_LAN)


@pytest.fixture(scope="module")
def db_trang():
    """Một cơ sở dữ liệu trắng đã chạy `alembic upgrade head`."""
    from app.config import settings

    ten_db = "test_migration_tu_dau"
    goc = settings.database_url.rsplit("/", 1)[0]
    quan_tri = create_engine(f"{goc}/postgres", isolation_level="AUTOCOMMIT")
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
        conn.execute(text(f"CREATE DATABASE {ten_db}"))

    url = f"{goc}/{ten_db}"
    # Không dùng check=True: nó ném ra ngoại lệ chỉ ghi mã thoát, còn nguyên nhân
    # thật thì nằm trong stderr và bị nuốt mất.
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


def test_moi_bang_trong_mo_hinh_deu_duoc_tao(db_trang):
    with db_trang.connect() as conn:
        co_that = {
            r[0] for r in conn.execute(
                text("select tablename from pg_tables where schemaname='public'")
            )
        }

    thieu = set(Base.metadata.tables) - co_that

    assert thieu == set()


def test_cot_embedding_dung_768_chieu(db_trang):
    with db_trang.connect() as conn:
        kieu = conn.execute(text(
            "select format_type(atttypid, atttypmod) from pg_attribute "
            "where attrelid = 'chunks'::regclass and attname = 'embedding'"
        )).scalar()

    assert kieu == "vector(768)"


def test_hai_vai_tro_han_che_quyen_duoc_tao(db_trang):
    with db_trang.connect() as conn:
        vai_tro = {
            r[0] for r in conn.execute(
                text("select rolname from pg_roles where rolname in ('app_runtime','app_worker')")
            )
        }

    assert vai_tro == {"app_runtime", "app_worker"}


def test_moi_bang_da_tenant_deu_bat_row_level_security(db_trang):
    with db_trang.connect() as conn:
        da_bat = {
            r[0] for r in conn.execute(
                text("select relname from pg_class where relrowsecurity")
            )
        }

    assert CAC_BANG_CO_RLS - da_bat == set()


def test_moi_bang_da_tenant_deu_co_policy(db_trang):
    with db_trang.connect() as conn:
        co_policy = {r[0] for r in conn.execute(text("select tablename from pg_policies"))}

    assert CAC_BANG_CO_RLS - co_policy == set()


def test_moi_bang_lan_qua_dung_cha_cua_no(db_trang):
    # Chi kiem tra "co policy" thi mot policy sai cha van lot qua, ma sai cha
    # nghia la loc nham hoac khong loc gi.
    with db_trang.connect() as conn:
        dieu_kien = {
            bang: qual for bang, qual in conn.execute(
                text("select tablename, qual from pg_policies")
            )
        }

    sai = {
        bang: dieu_kien.get(bang)
        for bang, manh in CHUOI_LAN.items()
        if not all(m in (dieu_kien.get(bang) or "") for m in manh)
    }

    assert sai == {}


def test_app_runtime_doc_ghi_duoc_bang_courses(db_trang):
    with db_trang.connect() as conn:
        quyen = {
            r[0] for r in conn.execute(text(
                "select privilege_type from information_schema.role_table_grants "
                "where grantee='app_runtime' and table_name='courses'"
            ))
        }

    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= quyen


def test_worker_khong_cham_duoc_vao_bang_nhay_cam(db_trang):
    # Worker co BYPASSRLS de xu ly tai lieu cua moi giang vien. Neu lai cap cho
    # no quyen tren users hay chat thi BYPASSRLS bien thanh lo hong, nen hai
    # dieu nay phai di cung nhau.
    with db_trang.connect() as conn:
        cham_duoc = {
            r[0] for r in conn.execute(text(
                "select distinct table_name from information_schema.role_table_grants "
                "where grantee='app_worker'"
            ))
        }

    assert cham_duoc & {"users", "chat_sessions", "chat_messages"} == set()


def test_worker_doc_duoc_han_token_nhung_khong_doc_duoc_chinh_token(db_trang):
    # Don token het han can SELECT tren cot trong menh de WHERE. Cap o muc cot
    # de viec do khong keo theo quyen doc token_hash.
    with db_trang.connect() as conn:
        doc_duoc = {
            r[0] for r in conn.execute(text(
                "select column_name from information_schema.role_column_grants "
                "where grantee='app_worker' and table_name='refresh_tokens' "
                "and privilege_type='SELECT'"
            ))
        }

    assert "expires_at" in doc_duoc and "revoked_at" in doc_duoc
    assert "token_hash" not in doc_duoc


def test_chi_worker_duoc_bo_qua_row_level_security(db_trang):
    with db_trang.connect() as conn:
        bo_qua = {
            r[0] for r in conn.execute(text(
                "select rolname from pg_roles "
                "where rolbypassrls and rolname in ('app_runtime','app_worker')"
            ))
        }

    assert bo_qua == {"app_worker"}
