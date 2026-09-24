"""devops/scripts/rotate_secrets.py changes every database and Moodle admin password in one go.

The parts that decide what ends up in .env and in Moodle's config.php are plain functions
and are tested here. The script must never print a password: the last tests run it and
look at everything it says."""

import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "devops" / "scripts" / "rotate_secrets.py"
spec = importlib.util.spec_from_file_location("rotate_secrets", SCRIPT)
rotate = importlib.util.module_from_spec(spec)
sys.modules["rotate_secrets"] = rotate
spec.loader.exec_module(rotate)

OLD = "old-shared-secret"
NEW = {
    "DB_PASSWORD": "NewOwnerPw-1",
    "APP_RUNTIME_DB_PASSWORD": "NewRuntimePw-2",
    "APP_WORKER_DB_PASSWORD": "NewWorkerPw-3",
    "MOODLE_ADMIN_PASSWORD": "NewAdminPw-4x",
}

ENV = f"""# database
DB_USER=owner
DB_PASSWORD={OLD}
DB_NAME=microlearning
DATABASE_URL=postgresql+psycopg://owner:{OLD}@localhost:5432/microlearning
APP_RUNTIME_DB_PASSWORD={OLD}
APP_RUNTIME_DATABASE_URL=postgresql+psycopg://app_runtime:{OLD}@localhost:5432/microlearning
DATABASE_URL_DOCKER=postgresql+psycopg://owner:{OLD}@db:5432/microlearning
APP_RUNTIME_DATABASE_URL_DOCKER=postgresql+psycopg://app_runtime:{OLD}@db:5432/microlearning
SECRET_KEY=keep-this-one
MOODLE_ADMIN_USER=admin
MOODLE_ADMIN_PASSWORD={OLD}
MOODLE_SITE_NAME="My Site"
"""


def _values(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))


def test_generated_passwords_are_long_random_and_need_no_quoting_anywhere():
    passwords = {rotate.generate_password() for _ in range(50)}

    assert len(passwords) == 50
    for password in passwords:
        assert len(password) >= 24
        # Safe in a URL, a SQL string, a PHP string and a shell without any escaping.
        assert re.fullmatch(r"[A-Za-z0-9_-]+", password)


def test_a_generated_moodle_admin_password_meets_moodles_default_policy():
    for _ in range(50):
        password = rotate.generate_password(for_moodle=True)

        assert len(password) >= 24
        assert re.search(r"[a-z]", password) and re.search(r"[A-Z]", password) and re.search(r"\d", password)
        assert re.search(r"[^A-Za-z0-9]", password)
        assert re.fullmatch(r"[A-Za-z0-9_-]+", password)


def test_the_four_password_variables_are_replaced():
    values = _values(rotate.update_env(ENV, NEW))

    assert {name: values[name] for name in NEW} == NEW


def test_the_passwords_inside_every_database_url_follow_them():
    values = _values(rotate.update_env(ENV, NEW))

    assert values["DATABASE_URL"] == f"postgresql+psycopg://owner:{NEW['DB_PASSWORD']}@localhost:5432/microlearning"
    assert values["DATABASE_URL_DOCKER"] == f"postgresql+psycopg://owner:{NEW['DB_PASSWORD']}@db:5432/microlearning"
    assert values["APP_RUNTIME_DATABASE_URL"] == (
        f"postgresql+psycopg://app_runtime:{NEW['APP_RUNTIME_DB_PASSWORD']}@localhost:5432/microlearning"
    )
    assert OLD not in rotate.update_env(ENV, NEW)


def test_urls_for_the_worker_are_added_from_the_runtime_ones_when_missing():
    values = _values(rotate.update_env(ENV, NEW))

    assert values["APP_WORKER_DATABASE_URL"] == (
        f"postgresql+psycopg://app_worker:{NEW['APP_WORKER_DB_PASSWORD']}@localhost:5432/microlearning"
    )
    assert values["APP_WORKER_DATABASE_URL_DOCKER"] == (
        f"postgresql+psycopg://app_worker:{NEW['APP_WORKER_DB_PASSWORD']}@db:5432/microlearning"
    )


def test_running_it_twice_changes_nothing_further_and_adds_nothing_twice():
    once = rotate.update_env(ENV, NEW)

    assert rotate.update_env(once, NEW) == once
    assert once.count("APP_WORKER_DATABASE_URL=") == 1


def test_lines_that_have_nothing_to_do_with_the_passwords_are_untouched():
    before, after = ENV.splitlines(), rotate.update_env(ENV, NEW).splitlines()
    kept = {"# database", "DB_USER=owner", "DB_NAME=microlearning", "SECRET_KEY=keep-this-one",
            "MOODLE_ADMIN_USER=admin", 'MOODLE_SITE_NAME="My Site"'}

    assert kept <= set(after)
    assert [line for line in before if line in kept] == [line for line in after if line in kept]


def test_the_line_endings_of_the_file_are_kept():
    crlf = ENV.replace("\n", "\r\n")

    updated = rotate.update_env(crlf, NEW)

    assert "\r\n" in updated
    assert re.search(r"(?<!\r)\n", updated) is None


def test_quotes_around_a_value_are_kept():
    quoted = ENV.replace(f"DB_PASSWORD={OLD}", f'DB_PASSWORD="{OLD}"')

    assert _values(rotate.update_env(quoted, NEW))["DB_PASSWORD"] == f'"{NEW["DB_PASSWORD"]}"'


def test_a_password_that_needs_quoting_is_refused_rather_than_written_into_a_url():
    with pytest.raises(ValueError):
        rotate.update_env(ENV, {**NEW, "DB_PASSWORD": "has@at:and/slash"})


CONFIG = """<?php
$CFG->dbhost    = 'db';
$CFG->dbuser    = 'owner';
$CFG->dbpass    = 'old-shared-secret';
$CFG->prefix    = 'mdl_';
"""


def test_only_the_database_password_line_of_moodles_config_changes():
    updated = rotate.update_moodle_config(CONFIG, "NewOwnerPw-1")

    assert "$CFG->dbpass    = 'NewOwnerPw-1';" in updated
    assert OLD not in updated
    assert [line for line in updated.splitlines() if "dbpass" not in line] == [
        line for line in CONFIG.splitlines() if "dbpass" not in line
    ]


def test_a_config_without_exactly_one_password_line_is_refused():
    with pytest.raises(ValueError):
        rotate.update_moodle_config("<?php\n$CFG->dbhost = 'db';\n", "x")
    with pytest.raises(ValueError):
        rotate.update_moodle_config(CONFIG + CONFIG, "x")


def test_a_dry_run_says_what_would_change_without_showing_or_touching_anything(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--env-file", str(env)],
        capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.returncode == 0, result.stderr
    everything = result.stdout + result.stderr
    assert OLD not in everything
    assert "DB_PASSWORD" in everything and "MOODLE_ADMIN_PASSWORD" in everything
    assert env.read_text(encoding="utf-8") == ENV


REPO_ROOT = SCRIPT.resolve().parents[2]


def _mac_dinh(ten_tham_so: str) -> pathlib.Path:
    defaults = rotate.build_parser().parse_args([])
    return pathlib.Path(getattr(defaults, ten_tham_so))


def test_mac_dinh_env_file_la_devops_env_o_goc_kho():
    """Neo vào đường dẫn cụ thể chứ không chỉ so với app.config: so hai chỗ với nhau thì cả
    hai cùng sai vẫn xanh. Mặc định này từng trỏ vào ".env" ở gốc kho, từ trước khi hai tệp
    .env được gộp làm một."""
    assert _mac_dinh("env_file") == REPO_ROOT / "devops" / ".env"


def test_mac_dinh_env_file_dung_tep_ma_backend_doc():
    """Rotate ghi mật khẩu mới vào một tệp mà ứng dụng không đọc thì ứng dụng vẫn dùng mật
    khẩu cũ và không kết nối được, mà không có gì báo sai ở đâu."""
    from app.config import _ENV_FILE

    assert _mac_dinh("env_file").resolve() == pathlib.Path(_ENV_FILE).resolve()


def test_mac_dinh_moodle_config_la_config_php_trong_cay_ma_moodle():
    """Mặc định này từng trỏ vào "moodle-src-523", tên cũ của cây mã Moodle, nên rotate sẽ
    lặng lẽ không đổi mật khẩu trong config.php của Moodle."""
    assert _mac_dinh("moodle_config") == REPO_ROOT / "moodle" / "config.php"


def test_mac_dinh_moodle_config_nam_trong_dung_thu_muc_compose_gan_lam_document_root():
    compose = yaml.safe_load((REPO_ROOT / "devops" / "docker-compose.yml").read_text(encoding="utf-8"))
    nguon = [
        str(v).split(":", 1)[0]
        for v in compose["services"]["moodle"]["volumes"]
        if str(v).split(":", 1)[1:2] == ["/var/www/html"]
    ]

    assert len(nguon) == 1, f"mong đợi đúng một mount làm document root của Moodle: {nguon}"
    assert _mac_dinh("moodle_config") == REPO_ROOT / nguon[0].removeprefix("./") / "config.php"
