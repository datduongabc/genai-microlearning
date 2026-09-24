"""Danh sách model mà giảng viên được chọn, đọc từ chính gateway.

Không ghi cứng danh sách trong mã: thêm một model là sửa devops/gateway/config.yaml,
và backend phải thấy ngay. Phần biến đổi được tách khỏi phần gọi mạng để kiểm được
trên dữ liệu tổng hợp phủ đủ các trường hợp, thay vì chỉ trên những gì gateway hiện
tại tình cờ trả về.
"""

import pytest

from app.llm import catalog


def _muc(alias: str, model: str, mode: str | None = "chat", **model_info) -> dict:
    thong_tin = {"mode": mode} if mode is not None else {}
    thong_tin.update(model_info)
    return {"model_name": alias, "litellm_params": {"model": model}, "model_info": thong_tin}


def test_chi_moi_model_sinh_van_ban_duoc_dem_ra_chon():
    """Alias nhúng lọt vào danh sách thì giảng viên chọn nó và cả pipeline sinh nội dung
    chết giữa đường bằng một lỗi không liên quan gì tới việc chọn model."""
    chon = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-flash-lite-latest"),
        _muc("embed-default", "gemini/gemini-embedding-001", mode="embedding"),
    ])

    assert [c.alias for c in chon] == ["gemini-fast"]


def test_model_khong_ro_che_do_thi_khong_dem_ra_chon():
    """Thà bỏ sót một model còn hơn đem một model nhúng ra cho chọn: bỏ sót thì giảng viên
    thấy thiếu và báo, còn chọn sai thì lỗi hiện ra ở chỗ khác hẳn."""
    chon = catalog.to_choices([_muc("la-lam", "abc/xyz", mode=None)])

    assert chon == []


def test_nhan_hien_thi_lay_tu_cau_hinh_gateway():
    chon = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-flash-lite-latest", display_name="Gemini Flash Lite")
    ])

    assert chon[0].label == "Gemini Flash Lite"


def test_thieu_nhan_thi_dung_chinh_alias_lam_nhan():
    """Thêm model ở gateway mà quên nhãn thì nó vẫn chọn được, chỉ là tên không đẹp."""
    chon = catalog.to_choices([_muc("groq-pro", "groq/openai/gpt-oss-120b")])

    assert chon[0].label == "groq-pro"


def test_ghi_chu_duoc_chuyen_tiep_de_giang_vien_biet_nen_chon_cai_nao():
    chon = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-flash-lite-latest", note="nhanh, miễn phí")
    ])

    assert chon[0].note == "nhanh, miễn phí"


def test_nha_cung_cap_suy_ra_tu_ten_model_chu_khong_phai_khai_them():
    """Suy ra từ tiền tố nên thêm model mới không phải khai lại nhà cung cấp. Groq đi qua
    litellm dưới dạng "groq/openai/..." nên phải lấy đúng đoạn đầu."""
    chon = catalog.to_choices([
        _muc("groq-pro", "groq/openai/gpt-oss-120b"),
        _muc("claude-pro", "anthropic/claude-sonnet-5"),
    ])

    assert {c.alias: c.provider for c in chon} == {"groq-pro": "groq", "claude-pro": "anthropic"}


def test_thu_tu_on_dinh_de_danh_sach_tren_giao_dien_khong_nhay():
    xuoi = catalog.to_choices([
        _muc("openai-fast", "openai/gpt-4o-mini"),
        _muc("gemini-pro", "gemini/gemini-pro-latest"),
        _muc("gemini-fast", "gemini/gemini-flash-lite-latest"),
    ])
    nguoc = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-flash-lite-latest"),
        _muc("gemini-pro", "gemini/gemini-pro-latest"),
        _muc("openai-fast", "openai/gpt-4o-mini"),
    ])

    assert [c.alias for c in xuoi] == [c.alias for c in nguoc]


def test_mot_alias_gateway_khong_phuc_vu_thi_khong_hop_le(monkeypatch):
    """Không tin client: gửi lên một alias lạ thì phải bị từ chối ở biên, chứ không để nó
    đi tới lệnh gọi LLM rồi hỏng ở đó."""
    monkeypatch.setattr(
        catalog, "available_models",
        lambda: catalog.to_choices([_muc("gemini-fast", "gemini/gemini-flash-lite-latest")]),
    )

    assert catalog.is_available("gemini-fast")
    assert not catalog.is_available("embed-default")
    assert not catalog.is_available("khong-ton-tai")


def test_danh_sach_duoc_ghi_dem_de_moi_lan_mo_trang_khong_goi_lai_gateway(monkeypatch):
    so_lan = {"n": 0}

    def dem():
        so_lan["n"] += 1
        return [_muc("gemini-fast", "gemini/gemini-flash-lite-latest")]

    monkeypatch.setattr(catalog, "fetch_model_info", dem)
    catalog.clear_cache()

    catalog.available_models()
    catalog.available_models()

    assert so_lan["n"] == 1


def test_ghi_dem_het_han_thi_hoi_lai_gateway(monkeypatch):
    """Sửa cấu hình gateway rồi khởi động lại nó thì backend phải thấy model mới mà không
    cần khởi động lại backend."""
    so_lan = {"n": 0}

    def dem():
        so_lan["n"] += 1
        return [_muc("gemini-fast", "gemini/gemini-flash-lite-latest")]

    monkeypatch.setattr(catalog, "fetch_model_info", dem)
    monkeypatch.setattr(catalog, "CACHE_SECONDS", 0)
    catalog.clear_cache()

    catalog.available_models()
    catalog.available_models()

    assert so_lan["n"] == 2


@pytest.fixture(autouse=True)
def _khong_de_cache_ro_ri_sang_test_khac():
    catalog.clear_cache()
    yield
    catalog.clear_cache()
