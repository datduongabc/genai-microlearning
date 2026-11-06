"""Lệnh khởi động trong backend/Dockerfile.

Hỏng ở đây không làm backend sập, nó làm backend âm thầm chạy mã cũ: bộ tự nạp lại chết
đi mà tiến trình phục vụ vẫn trả lời bình thường, nên sửa tệp xong gọi API vẫn ra kết quả
của bản trước, và người ta kết luận sai về chính mã mình vừa viết.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"


def _stage(name: str) -> str:
    """The lines of one stage: from its FROM to the next."""
    stages = re.split(r"^(?=FROM\s)", DOCKERFILE.read_text(encoding="utf-8"), flags=re.MULTILINE)
    found = [s for s in stages if re.match(rf"FROM\s+\S+\s+AS\s+{name}\s*$", s.splitlines()[0], re.IGNORECASE)]
    assert len(found) == 1, f"backend/Dockerfile phải có đúng một stage {name}"
    return found[0]


def _cmd(stage: str) -> list[str]:
    khop = re.findall(r"^CMD\s+(\[.*\])\s*$", _stage(stage), re.MULTILINE)
    assert len(khop) == 1, f"stage {stage} phải có đúng một CMD dạng JSON"
    return json.loads(khop[0])


def _lenh_khoi_dong() -> list[str]:
    """Stage dev, which compose runs over the bind mount."""
    return _cmd("dev")


def test_tu_nap_lai_chi_theo_doi_ma_ung_dung():
    """Không giới hạn thì bộ theo dõi phủ cả /app, gồm .venv với khoảng 34 nghìn tệp thư viện
    so với 116 tệp mã, và đã chết vì hết chỗ theo dõi (os error 12) trong khi backend vẫn
    phục vụ, tức là vẫn chạy mã cũ mà không có gì báo."""
    lenh = _lenh_khoi_dong()
    if "--reload" not in lenh:
        return

    thu_muc = [lenh[i + 1] for i, tham_so in enumerate(lenh[:-1]) if tham_so == "--reload-dir"]
    assert thu_muc, "có --reload thì phải giới hạn bằng --reload-dir"
    assert all(d.strip("./") == "app" for d in thu_muc), f"chỉ được theo dõi thư mục app/, đang theo dõi {thu_muc}"


def test_tu_nap_lai_do_dinh_ky_chu_khong_cho_su_kien_tu_he_dieu_hanh():
    """Mã được gắn vào container qua bind mount từ ổ đĩa Windows, và sự kiện "tệp bị sửa" không
    đi qua được lớp đó; chỉ sự kiện tạo và xoá tệp là đi qua. Bộ tự nạp lại chờ sự kiện thì
    thấy tệp mới nhưng không thấy tệp vừa sửa, tức backend vẫn chạy mã cũ sau mỗi lần sửa."""
    if "--reload" not in _lenh_khoi_dong():
        return

    env = re.findall(r"^ENV\s+(.+)$", _stage("dev"), re.MULTILINE)
    gia_tri = {k: v for dong in env for k, _, v in [dong.strip().partition("=")]}

    assert gia_tri.get("WATCHFILES_FORCE_POLLING", "").lower() == "true"


def test_the_last_stage_is_the_one_that_runs_on_ecs():
    """deploy.sh and the load test build without a target, which is the last stage."""
    last = re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE)[-1]
    assert last == "prod"


def test_prod_holds_neither_the_build_tools_nor_the_tests():
    """uv, the dev dependencies and tests/ only build or test the app; in the image that runs they are size and
    tools for whoever gets into it. The code is copied by name, so nothing new slips in with `COPY . .`."""
    prod = _stage("prod")
    command = _cmd("prod")

    assert "--reload" not in command and command[0] != "uv"
    assert not re.search(r"COPY\s+--from=uv\b", prod), "uv stays in the deps stage"
    assert not re.search(r"^COPY\s+(--\S+\s+)*\.\s", prod, re.MULTILINE), "no COPY of the whole context"
    assert "tests" not in prod
    assert "--no-dev" in _stage("deps")


def test_compose_builds_the_dev_stage():
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "devops" / "docker-compose.yml").read_text(encoding="utf-8"))
    built = {name: s["build"] for name, s in compose["services"].items()
             if isinstance(s.get("build"), (str, dict)) and "backend" in str(s["build"])}

    assert built and all(isinstance(b, dict) and b.get("target") == "dev" for b in built.values()), built


def test_the_ecs_commands_exist_in_the_prod_image():
    """The task definitions run these by absolute path: the venv must be where they look."""
    ecs = (REPO_ROOT / "infra" / "terraform" / "ecs.tf").read_text(encoding="utf-8")

    assert set(re.findall(r'"(/app/[^"]+)"', ecs)) <= {"/app/.venv/bin/uvicorn", "/app/.venv/bin/python",
                                                       "/app/.venv/bin/alembic"}
    assert "COPY --from=deps /app/.venv /app/.venv" in _stage("prod")


def test_ci_tests_on_the_python_the_image_runs():
    import yaml

    image = re.search(r"^ARG PYTHON_VERSION=(\S+)$", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE).group(1)
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [s for s in workflow["jobs"]["backend"]["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv@")]

    assert [str(s.get("with", {}).get("python-version")) for s in steps] == [image]
