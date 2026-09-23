"""Bản mẫu .env phải khai báo đủ và đúng những biến mà docker-compose cần.

Hai lỗi ở đây đều im lặng: Compose thay một biến không khai báo bằng chuỗi rỗng,
và khi một biến bị khai báo hai lần thì lần sau ghi đè lần trước. Trong cả hai
trường hợp stack vẫn khởi động khoẻ mạnh, chỉ đến lúc gọi nhà cung cấp mới lộ ra
dưới dạng lỗi 401 chẳng liên quan gì đến tệp .env."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "devops" / ".env.example"
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"

# $${TEN} là ký hiệu thoát: Compose truyền nguyên văn ${TEN} vào shell trong
# container chứ không tự thay, nên biến đó không cần có trong .env.
THAM_CHIEU_BIEN = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}")
KHAI_BAO_BIEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)


def _ten_bien_duoc_khai_bao() -> list[str]:
    return KHAI_BAO_BIEN.findall(ENV_EXAMPLE.read_text(encoding="utf-8"))


def test_khong_bien_nao_bi_khai_bao_hai_lan():
    ten = _ten_bien_duoc_khai_bao()

    trung = sorted({t for t in ten if ten.count(t) > 1})

    assert trung == [], f"biến bị khai báo nhiều lần, lần sau ghi đè lần trước: {trung}"


def test_moi_bien_compose_can_deu_co_trong_ban_mau():
    khai_bao = set(_ten_bien_duoc_khai_bao())

    # Biến có giá trị mặc định (${TEN:-...}) thiếu cũng không sao, Compose tự điền.
    bat_buoc = {ten for ten, mac_dinh in THAM_CHIEU_BIEN.findall(COMPOSE.read_text(encoding="utf-8")) if not mac_dinh}

    assert bat_buoc <= khai_bao, f"compose cần nhưng .env.example không khai báo: {sorted(bat_buoc - khai_bao)}"
