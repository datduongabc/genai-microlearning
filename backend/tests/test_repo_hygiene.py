"""Kho mã không được chứa bí mật hay cây mã của bên thứ ba.

Một lần commit tệp .env là khóa API nằm trong lịch sử git vĩnh viễn, nên việc này
được kiểm tra tự động thay vì trông chờ vào trí nhớ của người commit."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tracked_files() -> set[str]:
    # check=False: kho git chưa tồn tại là trường hợp hợp lệ, xử lý bằng skip bên dưới
    # thay vì để ném ngoại lệ.
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode != 0:
        pytest.skip("chưa có kho git")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def test_khong_co_tep_env_nao_duoc_theo_doi():
    tracked = _tracked_files()

    leaked = {p for p in tracked if Path(p).name == ".env" or p.endswith(".env")}

    assert leaked == set(), f"tệp bí mật bị theo dõi: {sorted(leaked)}"


def test_tep_env_example_van_duoc_theo_doi():
    # Bản mẫu không chứa giá trị thật và là tài liệu duy nhất nói rõ cần biến nào.
    assert "devops/.env.example" in _tracked_files()


def test_ma_nguon_moodle_khong_nam_trong_kho():
    tracked = _tracked_files()

    assert not [p for p in tracked if p.startswith("moodle/")]


def test_tai_lieu_mon_hoc_khong_nam_trong_kho():
    tracked = _tracked_files()

    assert not [p for p in tracked if p.startswith("documents/")]


def test_khong_co_thu_muc_phu_thuoc_nao_duoc_theo_doi():
    tracked = _tracked_files()
    rac = [p for p in tracked if "/node_modules/" in p or "/.venv/" in p or "__pycache__" in p]

    assert rac == []


def test_kho_ma_khong_phinh_qua_muc():
    # Toàn bộ mã nguồn của dự án vào khoảng vài nghìn tệp. Nếu vượt xa con số này
    # thì gần như chắc chắn một thư mục phụ thuộc đã lọt vào.
    assert len(_tracked_files()) < 2000
