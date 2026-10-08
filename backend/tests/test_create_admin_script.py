"""The first admin comes from a command on the server, never from the API."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from app.db import SessionLocal
from app.models import User

BACKEND = Path(__file__).resolve().parents[1]


def _run(email: str, password: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/create_admin.py", email, *args],
        cwd=BACKEND, capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": ".", "ADMIN_PASSWORD": password},
    )


def test_the_command_creates_an_admin_who_is_not_a_lecturer():
    email = f"cli-admin-{uuid.uuid4().hex[:8]}@Example.edu"

    result = _run(email, "A long enough phrase", "--name", "Quản trị viên")

    assert result.returncode == 0, result.stderr
    assert email.lower() in result.stdout
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email.lower()).one()
        assert user.is_admin == True and user.is_active and user.name == "Quản trị viên"


def test_the_command_refuses_a_weak_password_and_a_taken_email_and_never_prints_the_password():
    email = f"cli-admin-{uuid.uuid4().hex[:8]}@example.edu"

    weak = _run(email, "short")
    assert weak.returncode != 0 and "ít nhất 8 ký tự" in weak.stderr
    assert _run(email, "A long enough phrase").returncode == 0

    taken = _run(email, "Another long phrase 1")
    assert taken.returncode != 0 and "đã được đăng ký" in taken.stderr
    assert "A long enough phrase" not in taken.stdout + taken.stderr + weak.stdout + weak.stderr


def test_the_admin_can_use_any_valid_email():
    email = f"cli-admin-{uuid.uuid4().hex[:8]}@gmail.com"

    result = _run(email, "A long enough phrase")

    assert result.returncode == 0
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == email).one().is_admin
