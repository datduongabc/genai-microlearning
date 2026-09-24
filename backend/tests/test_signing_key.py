"""Khoá ký token chỉ được sinh tạm khi thật sự có người ký token.

Service migrate nạp app.config chỉ để lấy danh sách bảng, không bao giờ ký token, và cố
ý không được cấp SECRET_KEY vì không cần. Sinh khoá tạm ngay lúc nạp cấu hình làm mọi lần
migrate in ra cảnh báo "SECRET_KEY not set" vô nghĩa, và cảnh báo vô nghĩa lặp lại mỗi
ngày là thứ dạy người ta bỏ qua cảnh báo thật.
"""

import logging

import pytest

from app.config import Settings

CANH_BAO = "SECRET_KEY not set"


def _settings(**kwargs) -> Settings:
    # _env_file=None: không để .env thật trên máy lọt vào test.
    return Settings(_env_file=None, **kwargs)


def test_nap_cau_hinh_thieu_khoa_khong_canh_bao_gi(caplog):
    caplog.set_level(logging.WARNING, logger="app.config")

    _settings(secret_key="")

    assert CANH_BAO not in caplog.text


def test_lan_dau_can_ky_thi_moi_sinh_khoa_tam_va_canh_bao(caplog):
    caplog.set_level(logging.WARNING, logger="app.config")
    cau_hinh = _settings(secret_key="")

    khoa = cau_hinh.signing_key()

    assert len(khoa) >= 32
    assert CANH_BAO in caplog.text


def test_khoa_tam_giu_nguyen_giua_cac_lan_goi_va_chi_canh_bao_mot_lan(caplog):
    """Hai khoá khác nhau trong cùng một tiến trình thì token vừa ký xong đã không kiểm
    được, tức đăng nhập xong là bị đăng xuất ngay."""
    caplog.set_level(logging.WARNING, logger="app.config")
    cau_hinh = _settings(secret_key="")

    assert cau_hinh.signing_key() == cau_hinh.signing_key()
    assert caplog.text.count(CANH_BAO) == 1


def test_co_khoa_that_thi_dung_dung_khoa_do_va_khong_canh_bao(caplog):
    caplog.set_level(logging.WARNING, logger="app.config")
    cau_hinh = _settings(secret_key="khoa-that-" + "x" * 40)

    assert cau_hinh.signing_key() == "khoa-that-" + "x" * 40
    assert CANH_BAO not in caplog.text


def test_moi_truong_production_thieu_khoa_van_tu_choi_khoi_dong():
    """Không có đường lùi khoá tạm ở production: nhiều worker sẽ mỗi cái ký bằng một khoá,
    và mỗi lần khởi động lại sẽ đăng xuất tất cả mọi người."""
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _settings(environment="production", secret_key="")
