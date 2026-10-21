"""Section removal preserves modules/jobs/pairs and studies on a populated database."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.config import settings


def test_remove_textbook_scope_preserves_study_data_and_view_access():
    name = f"scope_migration_{uuid.uuid4().hex}"
    root = settings.database_url.rsplit("/", 1)[0]
    admin = create_engine(f"{root}/postgres", isolation_level="AUTOCOMMIT")
    engine = create_engine(f"{root}/{name}")

    def migrate(target, command="upgrade"):
        result = subprocess.run([sys.executable, "-m", "alembic", command, target],
                                cwd=Path(__file__).resolve().parents[2], capture_output=True,
                                text=True, timeout=120, check=False,
                                env={**os.environ, "DATABASE_URL": f"{root}/{name}"})
        assert result.returncode == 0, result.stderr

    ids = {key: uuid.uuid4() for key in ('owner', 'course', 'doc', 'section', 'rag', 'control',
                                        'component', 'question', 'job', 'queued', 'study', 'item')}
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    try:
        migrate("0020_study_judge")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id,email,name,hashed_password) VALUES (:owner,'scope@example.com','T','x')"), ids)
            conn.execute(text("INSERT INTO courses (id,owner_id,name) VALUES (:course,:owner,'Scope')"), ids)
            conn.execute(text("INSERT INTO documents (id,course_id,filename,status,kind) "
                              "VALUES (:doc,:course,'book.pdf','embedded','textbook')"), ids)
            conn.execute(text("INSERT INTO document_sections (id,document_id,title,level,position,first_chunk_index,last_chunk_index) "
                              "VALUES (:section,:doc,'Chapter',1,0,0,1)"), ids)
            conn.execute(text("INSERT INTO modules (id,document_id,course_id,title,section_id) "
                              "VALUES (:rag,:doc,:course,'Topic',:section)"), ids)
            conn.execute(text("INSERT INTO modules (id,document_id,course_id,title,section_id,paired_module_id) "
                              "VALUES (:control,:doc,:course,'Control',:section,:rag)"), ids)
            conn.execute(text("INSERT INTO module_components (id,module_id,type,content) "
                              "VALUES (:component,:rag,'quiz','{}')"), ids)
            conn.execute(text("INSERT INTO questions (id,component_id,stem,choices,answer_key,position) "
                              "VALUES (:question,:component,'Q','{\"options\":[\"A\",\"B\"]}','0',0)"), ids)
            conn.execute(text("INSERT INTO jobs (id,job_type,document_id,section_id,module_id,step,status,payload,attempts,max_attempts) "
                              "VALUES (:job,'module_generation',:doc,:section,:rag,'generate','failed','{}',5,5)"), ids)
            conn.execute(text("INSERT INTO jobs (id,job_type,document_id,section_id,step,status,payload,attempts,max_attempts) "
                              "VALUES (:queued,'module_generation',:doc,:section,'generate','queued','{}',0,5)"), ids)
            conn.execute(text("INSERT INTO studies (id,name,rubric_version) VALUES (:study,'Study','v1')"), ids)
            conn.execute(text("INSERT INTO study_items (id,study_id,\"group\",anon_code,module_id,packet) "
                              "VALUES (:item,:study,'rag_llm','M01',:rag,'{}')"), ids)
            access_sql = text("SELECT pg_get_viewdef('admin_failed_jobs'::regclass), "
                              "has_table_privilege('app_runtime','admin_failed_jobs','SELECT'), "
                              "pg_get_functiondef('retry_failed_job(uuid)'::regprocedure)")
            original_access = conn.execute(access_sql).one()
            tracked = ('modules', 'module_components', 'questions', 'jobs', 'study_items')
            original_rows = {table: conn.execute(text(f"SELECT * FROM {table}")).mappings().all() for table in tracked}
        migrate("0021_remove_textbook_scope")
        assert 'document_sections' not in inspect(engine).get_table_names()
        for table in ('documents', 'modules', 'jobs'):
            assert not {'kind', 'section_id'} & {c['name'] for c in inspect(engine).get_columns(table)}
        with engine.begin() as conn:
            assert conn.execute(access_sql).one() == original_access
            assert conn.execute(text("SELECT status, last_error FROM jobs WHERE id=:queued"), ids).one() == (
                'failed', 'Sinh theo mục đã bị gỡ bỏ; hãy sinh lại cho cả tài liệu.')
            for table, rows in original_rows.items():
                expected = [{k: v for k, v in row.items() if k != 'section_id'} for row in rows]
                actual = conn.execute(text(f"SELECT * FROM {table} WHERE id <> :queued"), ids).mappings().all()
                assert actual == [row for row in expected if row['id'] != ids['queued']]
            assert conn.scalar(text("SELECT count(*) FROM open_failed_jobs WHERE id=:job"), ids) == 1
            conn.execute(text("INSERT INTO jobs (id,job_type,document_id,step,status,payload,attempts,max_attempts,created_at) "
                              "VALUES (:id,'module_generation',:doc,'generate','queued','{}',0,5,now()+interval '1 second')"),
                         {**ids, 'id': uuid.uuid4()})
            assert conn.scalar(text("SELECT count(*) FROM open_failed_jobs WHERE id=:job"), ids) == 0
        migrate("0020_study_judge", "downgrade")
        assert 'document_sections' in inspect(engine).get_table_names()
        migrate("0021_remove_textbook_scope")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT module_id FROM study_items WHERE id=:item"), ids) == ids['rag']
            assert conn.scalar(text("SELECT paired_module_id FROM modules WHERE id=:control"), ids) == ids['rag']
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name}"))
        admin.dispose()
