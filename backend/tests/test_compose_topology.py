"""Who gets which secret and which port in devops/docker-compose.yml, and how requests reach the
application. The file is never run by the test suite, so a secret handed to the wrong
service or a port published on every interface would otherwise only be found by looking.

The process that serves requests and runs the worker gets what it needs and nothing else:
not the database owner's URL (only the migration step has it), not the Moodle admin
password, not the provider keys. The
proxy that terminates HTTPS is what the outside world talks to."""

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"
ROOT_COMPOSE = REPO_ROOT / "docker-compose.yml"
CADDYFILE = REPO_ROOT / "devops" / "caddy" / "Caddyfile"
ENTRYPOINT = REPO_ROOT / "backend" / "docker-entrypoint.sh"


@pytest.fixture(scope="module")
def services():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _environment(service: dict) -> dict:
    environment = service.get("environment", {})
    if isinstance(environment, list):
        return dict(item.split("=", 1) for item in environment)
    return environment


NOT_FOR_THE_BACKEND = (
    "DATABASE_URL", "DB_PASSWORD", "DB_USER", "MOODLE_ADMIN_PASSWORD", "MOODLE_ADMIN_USER",
    "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
)


def test_the_backend_is_not_handed_the_whole_env_file(services):
    assert "env_file" not in services["backend"]


@pytest.mark.parametrize("variable", NOT_FOR_THE_BACKEND)
def test_the_backend_does_not_receive(services, variable):
    assert variable not in _environment(services["backend"])


def test_the_backend_receives_the_roles_it_runs_as_and_not_the_owner(services):
    environment = _environment(services["backend"])

    assert "APP_RUNTIME_DATABASE_URL" in environment
    assert "APP_WORKER_DATABASE_URL" in environment
    assert "_DOCKER" in environment["APP_WORKER_DATABASE_URL"]


def test_only_the_migration_step_holds_the_owner_url_and_it_finishes_before_the_backend_starts(services):
    assert "DATABASE_URL" in _environment(services["migrate"])
    assert services["migrate"].get("restart") == "no"
    assert services["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert "alembic" not in ENTRYPOINT.read_text(encoding="utf-8")


def test_the_ports_published_by_default_stay_on_the_loopback_interface(services):
    for name in ("backend", "frontend", "gateway", "moodle"):
        for port in services[name].get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)
    for port in services["proxy"]["ports"]:
        # A public site moves the proxy off loopback with PROXY_BIND, and nothing else.
        assert str(port).startswith("${PROXY_BIND:-127.0.0.1}:"), port


def test_the_proxy_terminates_https_in_front_of_the_backend_and_frontend(services):
    proxy = services["proxy"]

    assert any(str(port).endswith(":443") for port in proxy["ports"])
    assert {"backend", "frontend"} <= set(proxy["depends_on"])
    assert any(str(volume).startswith("./devops/caddy/Caddyfile") for volume in proxy["volumes"])


def test_the_backend_believes_forwarded_addresses_only_from_the_compose_network(services):
    trusted = _environment(services["backend"])["FORWARDED_ALLOW_IPS"]

    assert "*" not in trusted
    assert "172.16.0.0/12" in trusted


def test_the_proxy_sends_the_api_and_the_pages_to_the_right_place():
    caddyfile = CADDYFILE.read_text(encoding="utf-8")

    assert "handle_path /api/*" in caddyfile
    assert "reverse_proxy backend:8000" in caddyfile
    assert "reverse_proxy frontend:5173" in caddyfile


def test_the_proxy_does_not_pin_a_local_name_to_https():
    # HSTS on "localhost" would also make browsers refuse Moodle's plain http port there.
    caddyfile = CADDYFILE.read_text(encoding="utf-8")

    assert "max-age={$HSTS_MAX_AGE:0}" in caddyfile


def test_the_compose_file_at_the_root_only_includes_the_one_in_devops():
    """`docker compose up -d` keeps working from the repository root: the file there
    includes devops/docker-compose.yml and keeps the root as the project directory, so
    relative paths and the project (and so volume) names are unchanged.

    The environment file lives next to the compose file it configures, in devops/, so that
    every piece of deployment configuration sits in one directory."""
    root = yaml.safe_load(ROOT_COMPOSE.read_text(encoding="utf-8"))

    assert set(root) == {"include"}
    (included,) = root["include"]
    assert included["path"] == "devops/docker-compose.yml"
    assert included["project_directory"] == "."
    assert included["env_file"] == "devops/.env"


def test_every_path_the_compose_file_builds_or_mounts_exists():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    missing = []
    for name, service in compose.items():
        build = service.get("build")
        if isinstance(build, dict):
            context = REPO_ROOT / build.get("context", ".")
            if not (context / build.get("dockerfile", "Dockerfile")).exists():
                missing.append((name, "dockerfile"))
        elif isinstance(build, str) and not (REPO_ROOT / build / "Dockerfile").exists():
            missing.append((name, "dockerfile"))
        for volume in service.get("volumes", []):
            source = str(volume).split(":", 1)[0]
            if source.startswith("./") and source not in NGUON_NGOAI_KHO and not (REPO_ROOT / source).exists():
                missing.append((name, source))

    assert missing == []


# Nguồn mount cố tình nằm ngoài kho mã, nên vắng mặt trong một bản clone mới và không
# được đòi phải tồn tại. Danh sách tường minh chứ không phải "cứ gitignore là bỏ qua":
# miễn trừ theo gitignore sẽ im lặng tha cho một đường dẫn thật sự cần, chỉ vì ai đó
# thêm nó vào .gitignore.
NGUON_NGOAI_KHO = frozenset({"./moodle"})


def _git_bo_qua(path: str) -> bool:
    """Hỏi với dấu gạch chéo cuối, và đó là điểm mấu chốt: mẫu trong .gitignore là `/moodle/`,
    có gạch cuối nên chỉ khớp thư mục. Hỏi `./moodle` thì git phải tự đoán đây có phải thư mục
    không, và nó đoán theo đĩa: trên máy đã chạy stack thì thư mục có thật nên khớp, còn trong
    một bản clone mới thì không có gì ở đó nên KHÔNG khớp. Bỏ dấu gạch cuối là dựng lại đúng
    cái bẫy "xanh ở máy mình, đỏ ở CI" mà hàm này sinh ra để chặn."""
    try:
        # check=False: mã thoát 1 chỉ có nghĩa "không bị bỏ qua", không phải lỗi.
        result = subprocess.run(
            ["git", "check-ignore", "-q", path.rstrip("/") + "/"],
            cwd=REPO_ROOT, capture_output=True, timeout=30, check=False,
        )
    except FileNotFoundError:
        pytest.skip("cần git để biết đường dẫn nào bị cố tình bỏ qua")
    return result.returncode == 0


def test_moi_nguon_duoc_mien_tru_deu_thuc_su_nam_ngoai_kho_ma():
    """Chặn việc dùng danh sách miễn trừ để che một đường dẫn đáng ra phải có trong kho.

    Đây cũng là chỗ bắt tên thư mục bị đổi: miễn trừ này từng ghi `./moodle-src`, cây mã
    Moodle sau đó đổi tên thành `./moodle`, tên ở đây không đổi theo, và CI đỏ ở mọi nhánh
    trong khi máy của người phát triển vẫn xanh vì thư mục có sẵn ở đó."""
    khong_bi_bo_qua = sorted(nguon for nguon in NGUON_NGOAI_KHO if not _git_bo_qua(nguon))

    assert khong_bi_bo_qua == [], (
        f"được miễn trừ nhưng git không bỏ qua, nên đáng ra phải có trong kho: {khong_bi_bo_qua}"
    )


def test_danh_sach_mien_tru_khong_con_muc_chet():
    """Một mục miễn trừ không còn được compose mount nữa thì chỉ là chỗ để lần sau ai đó vô
    tình dựa vào, nên phải bị gỡ cùng lúc với cái mount đã bỏ."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    dang_mount = {
        str(volume).split(":", 1)[0]
        for service in services.values()
        for volume in service.get("volumes", [])
    }

    assert NGUON_NGOAI_KHO <= dang_mount, f"miễn trừ không còn được mount: {sorted(NGUON_NGOAI_KHO - dang_mount)}"

