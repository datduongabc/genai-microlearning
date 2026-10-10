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
        source_policies = text("SELECT tablename, policyname, roles, cmd, qual, with_check "
                               "FROM pg_policies WHERE tablename IN "
                               "('courses', 'modules', 'module_components', 'documents', 'chunks', 'jobs') "
                               "ORDER BY tablename, policyname")
        with engine.connect() as conn:
            original_policies = conn.execute(source_policies).all()
        user_id, course_id, doc_id, rag_id, control_id = [uuid.uuid4() for _ in range(5)]
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id, email, name, hashed_password) VALUES (:id, :email, 'T', 'x')"),
                         {"id": user_id, "email": f"{user_id}@example.com"})
            conn.execute(text("INSERT INTO courses (id, owner_id, name) VALUES (:id, :owner, 'T')"),
                         {"id": course_id, "owner": user_id})
            conn.execute(text("INSERT INTO documents (id, course_id, filename, status) VALUES (:id, :course, 'a.txt', 'embedded')"),
                         {"id": doc_id, "course": course_id})
            for module_id, payload in ((rag_id, '{"model_alias":"test","self_check":false}'),
                                       (control_id, '{"model_alias":"test","self_check":false,"use_retrieval":false,'
                                                    f'"paired_module_id":"{rag_id}"' + '}')):
                conn.execute(text("INSERT INTO modules (id, document_id, course_id, title) VALUES (:id, :doc, :course, 'T')"),
                             {"id": module_id, "doc": doc_id, "course": course_id})
                conn.execute(text("INSERT INTO jobs (id, job_type, step, status, module_id, document_id, payload, attempts, max_attempts) "
                                  "VALUES (:id, 'module_generation', 'done', 'done', :module, :doc, CAST(:payload AS jsonb), 0, 5)"),
                             {"id": uuid.uuid4(), "module": module_id, "doc": doc_id, "payload": payload})
        migrate("head")
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT generation_payload->>'model_alias' FROM modules WHERE id=:id"),
                               {"id": rag_id}) == "test"
            assert conn.scalar(text("SELECT paired_module_id FROM modules WHERE id=:id"),
                               {"id": control_id}) == rag_id
            conn.execute(text("DELETE FROM jobs"))
            conn.execute(text("DELETE FROM modules WHERE id=:id"), {"id": rag_id})
            assert conn.scalar(text("SELECT count(*) FROM modules WHERE id=:id"), {"id": control_id}) == 0
        assert set(inspect(engine).get_table_names()) - previous == {"studies", "study_items", "ratings"}
        with engine.connect() as conn:
            assert conn.execute(source_policies).all() == original_policies
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
