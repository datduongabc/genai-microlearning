"""Quy ước dùng action trong .github/workflows/ci.yml.

Không kiểm "phiên bản phải là vN": một danh sách phiên bản viết trong test sẽ mục đi
đúng như mọi tên ghi cứng khác, và mỗi lần nâng action lại phải sửa hai chỗ. Chỉ kiểm
hai điều không mục: mọi action đều được ghim, và cùng một action thì mọi job dùng cùng
một phiên bản.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Ghim hợp lệ: vN, vN.N.N, hoặc SHA 40 ký tự. Nhánh như main hay master thì không.
GHIM_HOP_LE = re.compile(r"^(v\d+(\.\d+)*|[0-9a-f]{40})$")


@pytest.fixture(scope="module")
def cac_action() -> list[tuple[str, str]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    dung = []
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                ten, _, phien_ban = step["uses"].partition("@")
                dung.append((ten, phien_ban))
    return dung


def test_workflow_co_dung_action(cac_action):
    """Chặn hai test dưới trở thành rỗng nghĩa nếu cấu trúc tệp đổi và không đọc ra gì."""
    assert cac_action


def test_moi_action_deu_duoc_ghim_vao_mot_phien_ban(cac_action):
    """Trỏ vào một nhánh nghĩa là bản dựng hôm nay và hôm qua chạy mã khác nhau, nên một
    lần CI đỏ không nói được là do thay đổi của mình hay do action vừa đổi."""
    khong_ghim = sorted({f"{ten}@{pb or '(khong co)'}" for ten, pb in cac_action if not GHIM_HOP_LE.match(pb)})

    assert khong_ghim == [], f"action chưa được ghim vào phiên bản: {khong_ghim}"


def test_cung_mot_action_thi_moi_job_dung_cung_phien_ban(cac_action):
    """actions/checkout được dùng ở cả hai job. Để hai job lệch phiên bản thì một job sẽ
    lặng lẽ chạy trên runtime cũ và chỉ lộ ra dưới dạng cảnh báo hết hạn, không phải lỗi."""
    theo_ten: dict[str, set[str]] = {}
    for ten, phien_ban in cac_action:
        theo_ten.setdefault(ten, set()).add(phien_ban)

    lech = {ten: sorted(pb) for ten, pb in theo_ten.items() if len(pb) > 1}

    assert lech == {}, f"cùng một action nhưng các job dùng phiên bản khác nhau: {lech}"
