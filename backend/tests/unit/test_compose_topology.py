"""Who gets which secret and which port in devops/docker-compose.yml, and how requests reach the
application. The file is never run by the test suite, so a secret handed to the wrong
service or a port published on every interface would otherwise only be found by looking.

The process that serves requests and runs the worker gets what it needs and nothing else:
not the database owner's URL (only the migration step has it), not the Moodle admin
password, not the provider keys. The
proxy that terminates HTTPS is what the outside world talks to."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"
ROOT_COMPOSE = REPO_ROOT / "docker-compose.yml"
CADDYFILE = REPO_ROOT / "devops" / "caddy" / "Caddyfile"
ENTRYPOINT = REPO_ROOT / "backend" / "docker-entrypoint.sh"
ENV_EXAMPLE = REPO_ROOT / "devops" / ".env.example"

TIEN_TO = "do-an-"
THAM_CHIEU_BIEN = re.compile(
    r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::?([-?])[^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))"
)
KHAI_BAO_BIEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)


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
    "OPENROUTER_API_KEY", "GEMINI_API_KEY",
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


def test_jobs_run_in_the_worker_service_with_no_more_rights_than_the_api(services):
    api, worker = _environment(services["backend"]), _environment(services["worker"])

    assert api["RUN_JOB_WORKER"] == "false"
    assert worker == api
    assert "DATABASE_URL" not in worker
    assert "ports" not in services["worker"]


def test_api_containers_share_rate_limit_counters_in_a_private_redis(services):
    assert _environment(services["backend"])["RATE_LIMIT_STORAGE_URI"] == "redis://redis:6379/0"
    assert "ports" not in services["redis"]


def test_the_proxy_sends_the_api_and_the_pages_to_the_right_place():
    caddyfile = CADDYFILE.read_text(encoding="utf-8")

    assert "handle_path /api/*" in caddyfile
    assert "dynamic a backend 8000" in caddyfile
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


def test_every_path_the_compose_file_builds_or_mounts_exists(services):
    missing = []
    for name, service in services.items():
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


def test_danh_sach_mien_tru_khong_con_muc_chet(services):
    """Một mục miễn trừ không còn được compose mount nữa thì chỉ là chỗ để lần sau ai đó vô
    tình dựa vào, nên phải bị gỡ cùng lúc với cái mount đã bỏ."""
    dang_mount = {
        str(volume).split(":", 1)[0]
        for service in services.values()
        for volume in service.get("volumes", [])
    }

    assert NGUON_NGOAI_KHO <= dang_mount, f"miễn trừ không còn được mount: {sorted(NGUON_NGOAI_KHO - dang_mount)}"


def test_moi_service_deu_dat_ten_container_tuong_minh(services):
    """Để Compose tự sinh thì tên có thêm hậu tố số thứ tự và đổi theo tên thư mục
    làm việc, nên các lệnh docker exec viết sẵn sẽ hỏng khi kho được clone chỗ khác."""
    thieu = [ten for ten, service in services.items() if "container_name" not in service]

    assert thieu == [], f"service chưa đặt container_name: {thieu}"


def test_ten_container_luon_la_tien_to_cong_ten_service(services):
    lech = {
        ten: service["container_name"]
        for ten, service in services.items()
        if service.get("container_name") != TIEN_TO + ten
    }

    assert lech == {}, f"tên container không suy ra được từ tên service: {lech}"


def _thu_muc_build(service):
    build = service.get("build")
    if build is None:
        return None
    return build if isinstance(build, str) else build.get("context", ".") + "|" + build.get("dockerfile", "")


def test_cac_service_build_tu_cung_mot_cho_dung_chung_mot_image(services):
    """Không đặt `image` thì Compose tạo cho mỗi service một image riêng dù cùng
    Dockerfile. Bản thừa vừa chiếm thêm nguyên một ảnh nhiều GB, vừa có thể cũ hơn
    bản kia, nên bước migration chạy trên code khác với tiến trình nó chuẩn bị cho."""
    theo_thu_muc = {}
    for ten, service in services.items():
        thu_muc = _thu_muc_build(service)
        if thu_muc is not None:
            theo_thu_muc.setdefault(thu_muc, []).append((ten, service.get("image")))

    lech = {
        thu_muc: cac_service
        for thu_muc, cac_service in theo_thu_muc.items()
        if len(cac_service) > 1 and (len({image for _, image in cac_service}) != 1 or cac_service[0][1] is None)
    }

    assert lech == {}, f"các service cùng Dockerfile nhưng không chung image: {lech}"


def _ten_bien_duoc_khai_bao() -> list[str]:
    return KHAI_BAO_BIEN.findall(ENV_EXAMPLE.read_text(encoding="utf-8"))


def _bien_bat_buoc(noi_dung: str) -> set[str]:
    return {
        trong_ngoac or khong_ngoac
        for trong_ngoac, toan_tu, khong_ngoac in THAM_CHIEU_BIEN.findall(noi_dung)
        if toan_tu != "-"
    }


def test_khong_bien_nao_bi_khai_bao_hai_lan():
    ten = _ten_bien_duoc_khai_bao()

    trung = sorted({t for t in ten if ten.count(t) > 1})

    assert trung == [], f"biến bị khai báo nhiều lần, lần sau ghi đè lần trước: {trung}"


def test_moi_bien_compose_can_deu_co_trong_ban_mau():
    khai_bao = set(_ten_bien_duoc_khai_bao())

    bat_buoc = _bien_bat_buoc(COMPOSE.read_text(encoding="utf-8"))

    assert bat_buoc <= khai_bao, f"compose cần nhưng .env.example không khai báo: {sorted(bat_buoc - khai_bao)}"


def test_nhan_dien_duoc_moi_dang_noi_suy_cua_compose():
    mau = (
        "a: ${BAT_BUOC_NGOAC}\n"
        "b: $BAT_BUOC_KHONG_NGOAC\n"
        "c: ${BAT_BUOC_BAO_LOI:?phai co}\n"
        "d: ${BAT_BUOC_BAO_LOI_NGAN?phai co}\n"
        "e: ${CO_MAC_DINH:-x}\n"
        "f: ${CO_MAC_DINH_NGAN-x}\n"
        "g: $${DUOC_THOAT}\n"
    )

    assert _bien_bat_buoc(mau) == {
        "BAT_BUOC_NGOAC", "BAT_BUOC_KHONG_NGOAC", "BAT_BUOC_BAO_LOI", "BAT_BUOC_BAO_LOI_NGAN",
    }


def test_moodle_is_given_the_token_the_backend_uses(services):
    assert services["moodle"]["environment"]["MOODLE_TOKEN"] == "${MOODLE_TOKEN}"
    assert services["backend"]["environment"]["MOODLE_TOKEN"] == "${MOODLE_TOKEN}"

