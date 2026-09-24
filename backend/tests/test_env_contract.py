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

# Mọi dạng nội suy của Compose, không chỉ hai dạng đang dùng: dạng nào không nhận ra thì
# biến đó lặng lẽ thôi được canh, nên test sẽ báo ít lỗi hơn thực tế.
#   ${TEN} ${TEN:-md} ${TEN-md}   có hoặc không có mặc định
#   ${TEN:?loi} ${TEN?loi}        không có mặc định, Compose báo lỗi nếu thiếu
#   $TEN                          dạng không ngoặc
#   $${TEN}                       ký hiệu thoát, Compose truyền nguyên văn vào shell trong
#                                 container chứ không tự thay, nên không cần có trong .env
THAM_CHIEU_BIEN = re.compile(
    r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::?([-?])[^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))"
)
KHAI_BAO_BIEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)


def _ten_bien_duoc_khai_bao() -> list[str]:
    return KHAI_BAO_BIEN.findall(ENV_EXAMPLE.read_text(encoding="utf-8"))


def _bien_bat_buoc(noi_dung: str) -> set[str]:
    """Tên các biến mà thiếu là hỏng. Chỉ hai toán tử "-" và ":-" mới là mặc định; "?" và
    ":?" nghĩa là Compose dừng lại báo lỗi, nên biến đó vẫn bắt buộc."""
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
    """Canh chính phần nhận dạng ở trên. Compose hiện chỉ dùng ${TEN} và ${TEN:-md}, nên nếu
    chỉ đo trên tệp thật thì một dạng cú pháp không được nhận sẽ không hề lộ ra: test vẫn
    xanh trong khi nó đã thôi canh biến đó."""
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
