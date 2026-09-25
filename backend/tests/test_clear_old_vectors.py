"""Đổi model nhúng thì vector cũ phải mất đi, còn nội dung chunk thì giữ nguyên, rồi script nhúng lại
làm đầy chúng bằng model mới.

Vector cũ cùng 768 số nên database vẫn nhận và vẫn so, chỉ là thứ hạng vô nghĩa: không có lỗi nào lộ
ra nếu migration quên xoá, nên việc đó đáng có test riêng."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.models import EMBEDDING_DIM

BACKEND = Path(__file__).resolve().parents[1]
OLD = "[" + ",".join(["1"] + ["0"] * (EMBEDDING_DIM - 1)) + "]"


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, check=False, capture_output=True, text=True, timeout=300,
        env={**os.environ, "DATABASE_URL": url},
    )
    if result.returncode != 0:
        pytest.fail(f"alembic {' '.join(args)} thất bại:\n{result.stderr[-3000:]}")


@pytest.fixture(scope="module")
def db_url():
    """Một database trắng, dừng ở migration ngay trước bước xoá vector."""
    from app.config import settings

    name = "test_clear_old_vectors"
    root = settings.database_url.rsplit("/", 1)[0]
    admin = create_engine(f"{root}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = f"{root}/{name}"
    _alembic(url, "upgrade", "0004_question_position")

    yield url

    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
    admin.dispose()


def _chunks(engine):
    with engine.connect() as conn:
        return conn.execute(text("select content, embedding is null from chunks order by chunk_index")).all()


def test_upgrading_clears_every_vector_and_keeps_every_chunk(db_url):
    engine = create_engine(db_url)
    user, course, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text(
            "insert into users (id, email, name, hashed_password, role) values (:u, 'v@example.com', 'V', 'x', 'teacher')"
        ), {"u": user})
        conn.execute(text("insert into courses (id, name, owner_id) values (:c, 'C', :u)"), {"c": course, "u": user})
        conn.execute(text(
            "insert into documents (id, course_id, filename, status) values (:d, :c, 'a.pdf', 'embedded')"
        ), {"d": doc, "c": course})
        for i, vector in enumerate((OLD, OLD, None)):
            conn.execute(text(
                "insert into chunks (id, document_id, course_id, chunk_index, content, embedding) "
                "values (:id, :d, :c, :i, :content, cast(:v as vector))"
            ), {"id": uuid.uuid4(), "d": doc, "c": course, "i": i, "content": f"đoạn {i}", "v": vector})

    _alembic(db_url, "upgrade", "head")

    assert _chunks(engine) == [("đoạn 0", True), ("đoạn 1", True), ("đoạn 2", True)]
    engine.dispose()


def test_the_reembed_script_gives_every_cleared_chunk_a_vector_of_the_new_model(db_url, monkeypatch):
    """Chạy bằng vai trò worker: nó thấy chunk của mọi môn, không chỉ của một người dùng."""
    from app.config import settings
    from scripts import reembed_chunks

    worker_url = settings.app_worker_database_url.rsplit("/", 1)[0] + "/" + db_url.rsplit("/", 1)[1]
    worker = create_engine(worker_url)
    monkeypatch.setattr(reembed_chunks, "WorkerSessionLocal", sessionmaker(bind=worker))

    reembed_chunks.main()

    engine = create_engine(db_url)
    assert [cleared for _, cleared in _chunks(engine)] == [False, False, False]
    engine.dispose()
    worker.dispose()
