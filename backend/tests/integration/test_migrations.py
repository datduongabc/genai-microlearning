"""Database migration tests: round-trips, schema constraints, data preservation, and vector clearing."""

import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.generation.module_generation_pipeline import control_payload, matching_control
from app.models.models import EMBEDDING_DIM

BACKEND = Path(__file__).resolve().parents[2]
OLD_VECTOR = "[" + ",".join(["1"] + ["0"] * (EMBEDDING_DIM - 1)) + "]"


def _alembic(url: str, *args: str, timeout: int = 300) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "DATABASE_URL": url},
    )
    if result.returncode != 0:
        pytest.fail(f"alembic {' '.join(args)} thất bại:\n{result.stderr[-3000:]}")


@contextmanager
def _temp_database(prefix: str):
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    root = settings.database_url.rsplit("/", 1)[0]
    admin = create_engine(f"{root}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = f"{root}/{name}"
    engine = create_engine(url)
    try:
        yield url, engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        admin.dispose()


# --- 0036 retry_media_jobs migration (merged from test_retry_media_jobs_migration.py) ---


def test_0036_downgrades_to_the_function_without_the_media_branches_and_upgrades_again():
    """Migration 0036 can be undone, and redone: the downgrade is 0007's retry function, with no media branch."""
    with _temp_database("retry_media_migration") as (url, engine):
        def retry_function():
            with engine.connect() as conn:
                return conn.scalar(text("SELECT pg_get_functiondef('retry_failed_job(uuid)'::regprocedure)"))

        _alembic(url, "upgrade", "0036_retry_media_jobs", timeout=120)
        assert "flashcard_sets" in retry_function() and "videos" in retry_function()
        _alembic(url, "downgrade", "0035_flashcard_set_cost", timeout=120)
        assert "flashcard_sets" not in retry_function() and "documents" in retry_function()
        _alembic(url, "upgrade", "0036_retry_media_jobs", timeout=120)
        assert "flashcard_sets" in retry_function()


# --- Role enum removal migration (merged from test_role_enum_migration.py) ---


def test_local_accounts_have_a_boolean_admin_flag_without_deleted_state():
    with _temp_database("test_role_enum") as (url, engine):
        _alembic(url, "upgrade", "head")
        with engine.connect() as conn:
            columns = dict(
                conn.execute(
                    text(
                        "select attname, format_type(atttypid, atttypmod) "
                        "from pg_attribute where attrelid = 'users'::regclass "
                        "and attnum > 0 and not attisdropped"
                    )
                ).all()
            )
            assert columns["is_admin"] == "boolean"
            assert "deleted_at" not in columns
            assert "role" not in columns
            assert not conn.execute(text("select 1 from pg_type where typname='userrole'")).first()


# --- 0021 remove_textbook_scope migration (merged from test_remove_textbook_migration.py) ---


def test_remove_textbook_scope_preserves_study_data_and_view_access():
    """Section removal preserves modules/jobs/pairs and studies on a populated database."""
    ids = {
        key: uuid.uuid4()
        for key in (
            "owner", "course", "doc", "section", "rag", "control",
            "component", "question", "job", "queued", "study", "item",
        )
    }
    with _temp_database("scope_migration") as (url, engine):
        _alembic(url, "upgrade", "0020_study_judge", timeout=120)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id,email,name,hashed_password) VALUES (:owner,'scope@example.com','T','x')"),
                ids,
            )
            conn.execute(text("INSERT INTO courses (id,owner_id,name) VALUES (:course,:owner,'Scope')"), ids)
            conn.execute(
                text(
                    "INSERT INTO documents (id,course_id,filename,status,kind) "
                    "VALUES (:doc,:course,'book.pdf','embedded','textbook')"
                ),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO document_sections (id,document_id,title,level,position,first_chunk_index,last_chunk_index) "
                    "VALUES (:section,:doc,'Chapter',1,0,0,1)"
                ),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO modules (id,document_id,course_id,title,section_id) "
                    "VALUES (:rag,:doc,:course,'Topic',:section)"
                ),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO modules (id,document_id,course_id,title,section_id,paired_module_id) "
                    "VALUES (:control,:doc,:course,'Control',:section,:rag)"
                ),
                ids,
            )
            conn.execute(
                text("INSERT INTO module_components (id,module_id,type,content) VALUES (:component,:rag,'quiz','{}')"),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO questions (id,component_id,stem,choices,answer_key,position) "
                    "VALUES (:question,:component,'Q','{\"options\":[\"A\",\"B\"]}','0',0)"
                ),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO jobs (id,job_type,document_id,section_id,module_id,step,status,payload,attempts,max_attempts) "
                    "VALUES (:job,'module_generation',:doc,:section,:rag,'generate','failed','{}',5,5)"
                ),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO jobs (id,job_type,document_id,section_id,step,status,payload,attempts,max_attempts) "
                    "VALUES (:queued,'module_generation',:doc,:section,'generate','queued','{}',0,5)"
                ),
                ids,
            )
            conn.execute(text("INSERT INTO studies (id,name,rubric_version) VALUES (:study,'Study','v1')"), ids)
            conn.execute(
                text(
                    'INSERT INTO study_items (id,study_id,"group",anon_code,module_id,packet) '
                    "VALUES (:item,:study,'rag_llm','M01',:rag,'{}')"
                ),
                ids,
            )
            access_sql = text(
                "SELECT pg_get_viewdef('admin_failed_jobs'::regclass), "
                "has_table_privilege('app_runtime','admin_failed_jobs','SELECT'), "
                "pg_get_functiondef('retry_failed_job(uuid)'::regprocedure)"
            )
            original_access = conn.execute(access_sql).one()
            tracked = ("modules", "module_components", "questions", "jobs", "study_items")
            original_rows = {table: conn.execute(text(f"SELECT * FROM {table}")).mappings().all() for table in tracked}
        # Exactly the migration under test: the row comparison below would otherwise also see every
        # column a later migration adds to these tables.
        _alembic(url, "upgrade", "0021_remove_textbook_scope", timeout=120)
        assert "document_sections" not in inspect(engine).get_table_names()
        for table in ("documents", "modules", "jobs"):
            assert not {"kind", "section_id"} & {c["name"] for c in inspect(engine).get_columns(table)}
        with engine.begin() as conn:
            assert conn.execute(access_sql).one() == original_access
            assert conn.execute(text("SELECT status, last_error FROM jobs WHERE id=:queued"), ids).one() == (
                "failed",
                "Sinh theo mục đã bị gỡ bỏ; hãy sinh lại cho cả tài liệu.",
            )
            for table, rows in original_rows.items():
                expected = [{k: v for k, v in row.items() if k != "section_id"} for row in rows]
                actual = conn.execute(text(f"SELECT * FROM {table} WHERE id <> :queued"), ids).mappings().all()
                assert actual == [row for row in expected if row["id"] != ids["queued"]]
            assert conn.scalar(text("SELECT count(*) FROM open_failed_jobs WHERE id=:job"), ids) == 1
            conn.execute(
                text(
                    "INSERT INTO jobs (id,job_type,document_id,step,status,payload,attempts,max_attempts,created_at) "
                    "VALUES (:id,'module_generation',:doc,'generate','queued','{}',0,5,now()+interval '1 second')"
                ),
                {**ids, "id": uuid.uuid4()},
            )
            assert conn.scalar(text("SELECT count(*) FROM open_failed_jobs WHERE id=:job"), ids) == 0
        _alembic(url, "downgrade", "0020_study_judge", timeout=120)
        assert "document_sections" in inspect(engine).get_table_names()
        _alembic(url, "upgrade", "0021_remove_textbook_scope", timeout=120)
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT module_id FROM study_items WHERE id=:item"), ids) == ids["rag"]
            assert conn.scalar(text("SELECT paired_module_id FROM modules WHERE id=:control"), ids) == ids["rag"]


# --- Clear old vectors migration & reembed (merged from test_clear_old_vectors.py) ---


@pytest.fixture(scope="module")
def clear_vectors_db_url():
    """Một database trắng, dừng ở migration ngay trước bước xoá vector."""
    with _temp_database("test_clear_old_vectors") as (url, _):
        _alembic(url, "upgrade", "0004_question_position")
        yield url


def _chunks(engine):
    with engine.connect() as conn:
        return conn.execute(text("select content, embedding is null from chunks order by chunk_index")).all()


def test_upgrading_clears_every_vector_and_keeps_every_chunk(clear_vectors_db_url):
    engine = create_engine(clear_vectors_db_url)
    user, course, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("insert into users (id, email, name, hashed_password, role) values (:u, 'v@example.com', 'V', 'x', 'teacher')"),
            {"u": user},
        )
        conn.execute(text("insert into courses (id, name, owner_id) values (:c, 'C', :u)"), {"c": course, "u": user})
        conn.execute(
            text("insert into documents (id, course_id, filename, status) values (:d, :c, 'a.pdf', 'embedded')"),
            {"d": doc, "c": course},
        )
        for i, vector in enumerate((OLD_VECTOR, OLD_VECTOR, None)):
            conn.execute(
                text(
                    "insert into chunks (id, document_id, course_id, chunk_index, content, embedding) "
                    "values (:id, :d, :c, :i, :content, cast(:v as vector))"
                ),
                {"id": uuid.uuid4(), "d": doc, "c": course, "i": i, "content": f"đoạn {i}", "v": vector},
            )

    _alembic(clear_vectors_db_url, "upgrade", "head")

    assert _chunks(engine) == [("đoạn 0", True), ("đoạn 1", True), ("đoạn 2", True)]
    engine.dispose()


def test_the_reembed_script_gives_every_cleared_chunk_a_vector_of_the_new_model(clear_vectors_db_url, monkeypatch):
    """Chạy bằng vai trò worker: nó thấy chunk của mọi môn, không chỉ của một người dùng."""
    from scripts import reembed_chunks

    worker_url = settings.app_worker_database_url.rsplit("/", 1)[0] + "/" + clear_vectors_db_url.rsplit("/", 1)[1]
    worker = create_engine(worker_url)
    monkeypatch.setattr(reembed_chunks, "WorkerSessionLocal", sessionmaker(bind=worker))

    reembed_chunks.main()

    engine = create_engine(clear_vectors_db_url)
    assert [cleared for _, cleared in _chunks(engine)] == [False, False, False]
    engine.dispose()
    worker.dispose()


# --- Study migration round trip (merged from test_study_migration.py) ---


def test_study_migration_round_trip():
    """Exercise upgrade/downgrade on a fresh database, including runtime grants and RLS."""
    with _temp_database("study_migration") as (url, engine):
        _alembic(url, "upgrade", "0011_module_generation_models", timeout=120)
        previous = set(inspect(engine).get_table_names())
        source_policies = text(
            "SELECT tablename, policyname, roles, cmd, qual, with_check "
            "FROM pg_policies WHERE tablename IN "
            "('courses', 'modules', 'module_components', 'documents', 'chunks', 'jobs') "
            "ORDER BY tablename, policyname"
        )
        with engine.connect() as conn:
            original_policies = conn.execute(source_policies).all()
        user_id, course_id, doc_id, rag_id, control_id = [uuid.uuid4() for _ in range(5)]
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email, name, hashed_password) VALUES (:id, :email, 'T', 'x')"),
                {"id": user_id, "email": f"{user_id}@example.com"},
            )
            conn.execute(
                text("INSERT INTO courses (id, owner_id, name) VALUES (:id, :owner, 'T')"),
                {"id": course_id, "owner": user_id},
            )
            conn.execute(
                text("INSERT INTO documents (id, course_id, filename, status) VALUES (:id, :course, 'a.txt', 'embedded')"),
                {"id": doc_id, "course": course_id},
            )
            for module_id, payload in (
                (rag_id, '{"model_alias":"test","self_check":false}'),
                (
                    control_id,
                    (
                        '{"model_alias":"test","self_check":false,"use_retrieval":false,'
                        f'"paired_module_id":"{rag_id}"'
                        "}"
                    ),
                ),
            ):
                conn.execute(
                    text("INSERT INTO modules (id, document_id, course_id, title) VALUES (:id, :doc, :course, 'T')"),
                    {"id": module_id, "doc": doc_id, "course": course_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO jobs (id, job_type, step, status, module_id, document_id, payload, attempts, max_attempts) "
                        "VALUES (:id, 'module_generation', 'done', 'done', :module, :doc, CAST(:payload AS jsonb), 0, 5)"
                    ),
                    {"id": uuid.uuid4(), "module": module_id, "doc": doc_id, "payload": payload},
                )
        _alembic(url, "upgrade", "head", timeout=120)
        with engine.begin() as conn:
            assert (
                conn.scalar(text("SELECT generation_payload->>'model_alias' FROM modules WHERE id=:id"), {"id": rag_id})
                == "test"
            )
            assert conn.scalar(text("SELECT paired_module_id FROM modules WHERE id=:id"), {"id": control_id}) == rag_id
            expected = control_payload(rag_id, "test", False)
            assert conn.scalar(text("SELECT generation_payload FROM modules WHERE id=:id"), {"id": control_id}) == {
                **expected,
                "custom_instructions": "",
            }
            with Session(conn) as db:
                assert matching_control(db, rag_id, expected).id == control_id
                assert matching_control(db, rag_id, control_payload(rag_id, "test", False, "en")) is None
            conn.execute(text("DELETE FROM jobs"))
            with Session(conn) as db:
                assert matching_control(db, rag_id, expected).id == control_id
            conn.execute(text("DELETE FROM modules WHERE id=:id"), {"id": rag_id})
            assert conn.scalar(text("SELECT count(*) FROM modules WHERE id=:id"), {"id": control_id}) == 0
        # head also has what later migrations added: LTI (0026), course membership (0027) and the
        # studies' reference files (0031) and videos and flashcard sets (0032).
        lti = {
            "lti_platforms", "lti_deployments", "lti_identities", "lti_contexts",
            "lti_login_states", "lti_launch_codes", "course_members", "study_files",
            "videos", "flashcard_sets",
        }
        assert set(inspect(engine).get_table_names()) - previous - lti == {"studies", "study_items", "ratings"}
        assert previous - set(inspect(engine).get_table_names()) == {"document_sections"}
        # Downgrade judge permissions while preserving blind rating.
        _alembic(url, "downgrade", "0019_blind_ratings", timeout=120)
        assert "judge_model" not in {c["name"] for c in inspect(engine).get_columns("ratings")}
        with engine.connect() as conn:
            assert conn.execute(source_policies).all() == original_policies
            for table in ("studies", "study_items", "ratings"):
                assert conn.scalar(text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table)"), {"table": table})
                assert conn.scalar(
                    text("SELECT has_table_privilege('app_runtime', :table, 'SELECT,INSERT,UPDATE,DELETE')"),
                    {"table": table},
                )
                assert not conn.scalar(
                    text("SELECT has_table_privilege('app_worker', :table, 'SELECT')"), {"table": table}
                )
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0019_blind_ratings"
        _alembic(url, "downgrade", "0011_module_generation_models", timeout=120)
        assert set(inspect(engine).get_table_names()) == previous
        _alembic(url, "upgrade", "head", timeout=120)
        assert {"studies", "study_items", "ratings"}.issubset(inspect(engine).get_table_names())
        # 0020: the queued judge reaches the study tables as the worker, admins see only judge jobs.
        judge_policy = text("SELECT qual FROM pg_policies WHERE tablename = 'jobs' AND policyname = 'jobs_study_admin'")
        with engine.connect() as conn:
            assert "study_judge" in conn.scalar(judge_policy)
            assert conn.scalar(text("SELECT has_table_privilege('app_worker', 'ratings', 'SELECT,INSERT,UPDATE')"))
            assert not conn.scalar(text("SELECT has_table_privilege('app_worker', 'ratings', 'DELETE')"))
            assert conn.scalar(text("SELECT has_table_privilege('app_worker', 'study_items', 'SELECT')"))
            assert not conn.scalar(text("SELECT has_table_privilege('app_worker', 'study_items', 'UPDATE')"))
