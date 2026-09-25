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


def _thu_muc_build(service):
    build = service.get("build")
    if build is None:
        return None
    return build if isinstance(build, str) else build.get("context", ".") + "|" + build.get("dockerfile", "")


def test_cac_service_build_tu_cung_mot_cho_dung_chung_mot_image(compose):
    """Không đặt `image` thì Compose tạo cho mỗi service một image riêng dù cùng
    Dockerfile. Bản thừa vừa chiếm thêm nguyên một ảnh nhiều GB, vừa có thể cũ hơn
    bản kia, nên bước migration chạy trên code khác với tiến trình nó chuẩn bị cho."""
    theo_thu_muc = {}
    for ten, service in compose["services"].items():
        thu_muc = _thu_muc_build(service)
        if thu_muc is not None:
            theo_thu_muc.setdefault(thu_muc, []).append((ten, service.get("image")))

    lech = {
        thu_muc: cac_service
        for thu_muc, cac_service in theo_thu_muc.items()
        if len(cac_service) > 1 and (len({image for _, image in cac_service}) != 1 or cac_service[0][1] is None)
    }

    assert lech == {}, f"các service cùng Dockerfile nhưng không chung image: {lech}"
