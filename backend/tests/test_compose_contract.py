"""Quy ước đặt tên trong devops/docker-compose.yml.

Tên container được đặt tay chứ không để Compose tự sinh, nên nó có thể lệch khỏi
tên service mà không có gì báo. Khi đã lệch thì `docker compose exec <service>` và
`docker exec <container>` không còn suy ra được từ nhau, và mọi ghi chú hay lệnh
chép lại từ trước đó đều trỏ sai chỗ.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"

TIEN_TO = "do-an-"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_moi_service_deu_dat_ten_container_tuong_minh(compose):
    """Để Compose tự sinh thì tên có thêm hậu tố số thứ tự và đổi theo tên thư mục
    làm việc, nên các lệnh docker exec viết sẵn sẽ hỏng khi kho được clone chỗ khác."""
    thieu = [ten for ten, service in compose["services"].items() if "container_name" not in service]

    assert thieu == [], f"service chưa đặt container_name: {thieu}"


def test_ten_container_luon_la_tien_to_cong_ten_service(compose):
    lech = {
        ten: service["container_name"]
        for ten, service in compose["services"].items()
        if service.get("container_name") != TIEN_TO + ten
    }

    assert lech == {}, f"tên container không suy ra được từ tên service: {lech}"
