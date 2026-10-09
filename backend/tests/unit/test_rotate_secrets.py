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

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "devops" / "scripts" / "rotate_secrets.py"
spec = importlib.util.spec_from_file_location("rotate_secrets", SCRIPT)
rotate = importlib.util.module_from_spec(spec)
sys.modules["rotate_secrets"] = rotate
spec.loader.exec_module(rotate)

OLD = "old-shared-secret"
NEW = {
    "DATABASE_PASSWORD": "NewOwnerPw-1",
    "APP_RUNTIME_DB_PASSWORD": "NewRuntimePw-2",
    "APP_WORKER_DB_PASSWORD": "NewWorkerPw-3",
    "MOODLE_ADMIN_PASSWORD": "NewAdminPw-4x",
}

ENV = f"""# database
DATABASE_USER=owner
DATABASE_PASSWORD={OLD}
DATABASE_NAME=microlearning
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

    assert values["DATABASE_URL"] == f"postgresql+psycopg://owner:{NEW['DATABASE_PASSWORD']}@localhost:5432/microlearning"
    assert values["DATABASE_URL_DOCKER"] == f"postgresql+psycopg://owner:{NEW['DATABASE_PASSWORD']}@db:5432/microlearning"
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
    kept = {"# database", "DATABASE_USER=owner", "DATABASE_NAME=microlearning", "SECRET_KEY=keep-this-one",
            "MOODLE_ADMIN_USER=admin", 'MOODLE_SITE_NAME="My Site"'}

    assert kept <= set(after)
    assert [line for line in before if line in kept] == [line for line in after if line in kept]


def test_the_line_endings_of_the_file_are_kept():
    crlf = ENV.replace("\n", "\r\n")

    updated = rotate.update_env(crlf, NEW)

    assert "\r\n" in updated
    assert re.search(r"(?<!\r)\n", updated) is None


def test_quotes_around_a_value_are_kept():
    quoted = ENV.replace(f"DATABASE_PASSWORD={OLD}", f'DATABASE_PASSWORD="{OLD}"')

    assert _values(rotate.update_env(quoted, NEW))["DATABASE_PASSWORD"] == f'"{NEW["DATABASE_PASSWORD"]}"'


def test_a_password_that_needs_quoting_is_refused_rather_than_written_into_a_url():
    with pytest.raises(ValueError):
        rotate.update_env(ENV, {**NEW, "DATABASE_PASSWORD": "has@at:and/slash"})


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
    assert "DATABASE_PASSWORD" in everything and "MOODLE_ADMIN_PASSWORD" in everything
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


# --- neo vào bản mẫu thật ------------------------------------------------------
# Các test phía trên dùng một .env tổng hợp, đủ để kiểm cơ chế thay thế nhưng không nói
# được script có khớp .env thật không. Chính chỗ hở đó đã để script tìm DB_USER suốt
# thời gian .env dùng DATABASE_USER: test vẫn xanh vì .env tổng hợp cũng dùng DB_USER.
# Các test dưới đây dựng .env từ devops/.env.example, tệp duy nhất khai tên biến thật.

BAN_MAU = REPO_ROOT / "devops" / ".env.example"


def _env_tu_ban_mau(moodle_dung_chung_vai_tro_chu: bool) -> str:
    """Bản mẫu với mọi mật khẩu điền giá trị cũ. `moodle_dung_chung_vai_tro_chu` dựng lại
    đúng cách stack đang chạy: Moodle đăng nhập database bằng vai trò chủ sở hữu."""
    dong = []
    for line in BAN_MAU.read_text(encoding="utf-8").splitlines():
        if re.fullmatch(r"[A-Z_]+_PASSWORD=", line):
            line += OLD
        dong.append(line.replace("<mat-khau>", OLD))
    text = "\n".join(dong) + "\n"
    if moodle_dung_chung_vai_tro_chu:
        chu = re.search(r"^DATABASE_USER=(.*)$", text, re.MULTILINE).group(1)
        text = re.sub(r"^MOODLE_DB_USER=.*$", f"MOODLE_DB_USER={chu}", text, flags=re.MULTILINE)
    return text


def test_chay_thu_duoc_tren_env_dung_hinh_dang_ban_mau(tmp_path):
    """Đúng lỗi đã xảy ra: script từ chối chạy trên .env thật vì tìm DB_USER, nên mật khẩu
    phải rotate tay và bản sao của Moodle bị bỏ sót, làm Moodle không kết nối được."""
    env = tmp_path / ".env"
    env.write_text(_env_tu_ban_mau(moodle_dung_chung_vai_tro_chu=True), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--env-file", str(env)],
        capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert OLD not in result.stdout + result.stderr
    # Người chạy phải biết trước rằng mật khẩu của Moodle cũng đổi theo, không chỉ bốn cái
    # được yêu cầu, để khỏi ngạc nhiên khi thấy config.php của Moodle bị sửa.
    assert "MOODLE_DB_PASSWORD" in result.stdout


def test_moodle_dung_chung_vai_tro_chu_thi_mat_khau_cua_no_doi_theo():
    """Moodle đăng nhập bằng vai trò chủ sở hữu, nên MOODLE_DB_PASSWORD là bản sao thứ hai
    của cùng một bí mật. Không đổi theo thì lần tạo lại config.php kế tiếp ghi mật khẩu cũ."""
    moi = _values(rotate.update_env(_env_tu_ban_mau(moodle_dung_chung_vai_tro_chu=True), NEW))

    assert moi["MOODLE_DB_PASSWORD"] == NEW["DATABASE_PASSWORD"]


def test_moodle_co_vai_tro_rieng_thi_khong_dung_toi_mat_khau_cua_no():
    moi = _values(rotate.update_env(_env_tu_ban_mau(moodle_dung_chung_vai_tro_chu=False), NEW))

    assert moi["MOODLE_DB_PASSWORD"] == OLD


def test_moi_url_database_cua_ban_mau_deu_theo_mat_khau_moi():
    moi = rotate.update_env(_env_tu_ban_mau(moodle_dung_chung_vai_tro_chu=True), NEW)

    urls = {k: v for k, v in _values(moi).items() if "DATABASE_URL" in k}
    assert urls, "bản mẫu phải có URL database"
    assert all(OLD not in v for v in urls.values()), f"URL còn mật khẩu cũ: {sorted(k for k, v in urls.items() if OLD in v)}"


def test_config_php_chi_nhan_mat_khau_chu_khi_moodle_dung_chung_vai_tro():
    """Trước đây script luôn ghi mật khẩu chủ sở hữu vào config.php, kể cả khi Moodle có vai
    trò riêng, tức là làm hỏng Moodle đúng lúc nó đang chạy đúng."""
    dung_chung = rotate.parse_env(_env_tu_ban_mau(moodle_dung_chung_vai_tro_chu=True))
    rieng = rotate.parse_env(_env_tu_ban_mau(moodle_dung_chung_vai_tro_chu=False))

    assert rotate.moodle_uses_owner_role(dung_chung)
    assert not rotate.moodle_uses_owner_role(rieng)


def test_script_goi_database_va_moodle_qua_ten_service_co_that_trong_compose():
    """Gọi qua tên service chứ không qua tên container: script từng gọi "moodle_postgres" và
    "moodle_app" rất lâu sau khi container đã đổi tên, và tên container còn đổi theo nhánh."""
    services = yaml.safe_load((REPO_ROOT / "devops" / "docker-compose.yml").read_text(encoding="utf-8"))["services"]

    assert rotate.DB_SERVICE in services
    assert rotate.MOODLE_SERVICE in services
