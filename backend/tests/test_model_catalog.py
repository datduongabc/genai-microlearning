"""Danh sách model mà giảng viên được chọn, đọc từ chính gateway.

Không ghi cứng danh sách trong mã: thêm một model là sửa devops/gateway/config.yaml,
và backend phải thấy ngay. Phần biến đổi được tách khỏi phần gọi mạng để kiểm được
trên dữ liệu tổng hợp phủ đủ các trường hợp, thay vì chỉ trên những gì gateway hiện
tại tình cờ trả về.
"""

import litellm
import litellm.exceptions as loi_litellm
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
        _muc("gemini-fast", "gemini/gemini-3.5-flash-lite"),
        _muc("embed-default", "gemini/gemini-embedding-001", mode="embedding"),
    ])

    assert [c.alias for c in chon] == ["gemini-fast"]


def test_alias_khai_generation_false_thi_khong_dem_ra_chon_nhung_van_o_gateway():
    """groq-fast (gpt-oss-20b) không sinh nổi mô-đun nhưng vẫn dùng để chấm điểm: ẩn khỏi danh sách
    giảng viên chọn, còn gateway vẫn liệt kê nó cho các script chấm điểm."""
    raw = [
        _muc("gemini-fast", "gemini/gemini-3.5-flash-lite"),
        _muc("groq-fast", "groq/openai/gpt-oss-20b", generation=False),
        _muc("groq-pro", "groq/openai/gpt-oss-120b", generation=True),
    ]

    chon = catalog.to_choices(raw)

    assert [c.alias for c in chon] == ["gemini-fast", "groq-pro"]
    assert "groq-fast" in {m["model_name"] for m in raw}


def test_model_khong_ro_che_do_thi_khong_dem_ra_chon():
    """Thà bỏ sót một model còn hơn đem một model nhúng ra cho chọn: bỏ sót thì giảng viên
    thấy thiếu và báo, còn chọn sai thì lỗi hiện ra ở chỗ khác hẳn."""
    chon = catalog.to_choices([_muc("la-lam", "abc/xyz", mode=None)])

    assert chon == []


def test_nhan_hien_thi_lay_tu_cau_hinh_gateway():
    chon = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-3.5-flash-lite", display_name="Gemini Flash Lite")
    ])

    assert chon[0].label == "Gemini Flash Lite"


def test_thieu_nhan_thi_dung_chinh_alias_lam_nhan():
    """Thêm model ở gateway mà quên nhãn thì nó vẫn chọn được, chỉ là tên không đẹp."""
    chon = catalog.to_choices([_muc("groq-pro", "groq/openai/gpt-oss-120b")])

    assert chon[0].label == "groq-pro"


def test_ghi_chu_duoc_chuyen_tiep_de_giang_vien_biet_nen_chon_cai_nao():
    chon = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-3.5-flash-lite", note="nhanh, miễn phí")
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
        _muc("gemini-flash", "gemini/gemini-3.5-flash"),
        _muc("gemini-fast", "gemini/gemini-3.5-flash-lite"),
    ])
    nguoc = catalog.to_choices([
        _muc("gemini-fast", "gemini/gemini-3.5-flash-lite"),
        _muc("gemini-flash", "gemini/gemini-3.5-flash"),
        _muc("openai-fast", "openai/gpt-4o-mini"),
    ])

    assert [c.alias for c in xuoi] == [c.alias for c in nguoc]


def test_mot_alias_gateway_khong_phuc_vu_thi_khong_hop_le(monkeypatch):
    """Không tin client: gửi lên một alias lạ thì phải bị từ chối ở biên, chứ không để nó
    đi tới lệnh gọi LLM rồi hỏng ở đó."""
    monkeypatch.setattr(
        catalog, "available_models",
        lambda: catalog.to_choices([_muc("gemini-fast", "gemini/gemini-3.5-flash-lite")]),
    )

    assert catalog.is_available("gemini-fast")
    assert not catalog.is_available("embed-default")
    assert not catalog.is_available("khong-ton-tai")


def test_danh_sach_duoc_ghi_dem_de_moi_lan_mo_trang_khong_goi_lai_gateway(monkeypatch):
    so_lan = {"n": 0}

    def dem():
        so_lan["n"] += 1
        return [_muc("gemini-fast", "gemini/gemini-3.5-flash-lite")]

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
        return [_muc("gemini-fast", "gemini/gemini-3.5-flash-lite")]

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


# --- thử nhanh model trước khi xếp hàng lượt sinh ----------------------------
# Model có trong danh sách chưa chắc đã dùng được: khóa của nhà cung cấp có thể trống,
# hết số dư, hoặc hết hạn mức ngày. Thử một lệnh rất nhỏ lúc bấm sinh để báo ngay, thay
# vì để lượt sinh chạy mười phút rồi mới hỏng.

@pytest.fixture
def ghi_lai_lenh_thu(monkeypatch):
    goi = {}

    def gia_lap(**kwargs):
        goi.update(kwargs)

    monkeypatch.setattr(litellm, "completion", gia_lap)
    return goi


def _nem(ngoai_le):
    def gia_lap(**kwargs):
        raise ngoai_le

    return gia_lap


def test_thu_thanh_cong_thi_khong_bao_gi(ghi_lai_lenh_thu):
    catalog.probe("groq-pro")


def test_thu_dung_model_da_chon_va_chi_xin_mot_token(ghi_lai_lenh_thu):
    """Một token là đủ để biết khóa và số dư còn dùng được, mà không tốn hạn mức đáng kể."""
    catalog.probe("groq-pro")

    assert ghi_lai_lenh_thu["model"] == "openai/groq-pro"
    assert ghi_lai_lenh_thu["max_tokens"] == 1


def test_khoa_trong_hoac_sai_thi_bao_chua_co_khoa(monkeypatch):
    monkeypatch.setattr(litellm, "completion", _nem(
        loi_litellm.AuthenticationError(message="401", llm_provider="openai", model="gpt-4o")
    ))

    with pytest.raises(catalog.ModelUnusable) as bat:
        catalog.probe("openai-pro")

    assert bat.value.alias == "openai-pro"
    assert "api key" in bat.value.ly_do.lower()


def test_het_han_muc_thi_bao_het_han_muc(monkeypatch):
    """Đúng lỗi đã gặp: hạn mức ngày của Gemini cạn giữa chừng một lượt chạy."""
    monkeypatch.setattr(litellm, "completion", _nem(
        loi_litellm.RateLimitError(message="429", llm_provider="gemini", model="gemini-flash")
    ))

    with pytest.raises(catalog.ModelUnusable) as bat:
        catalog.probe("gemini-flash")

    assert "hạn mức" in bat.value.ly_do


def test_khoa_trong_that_ra_500_chu_khong_phai_401_van_bao_dung_la_thieu_khoa(monkeypatch):
    """Hình dạng lỗi đo trên gateway thật của stack này, không phải hình dạng người ta hay
    tưởng: khóa OpenAI trống thì gateway hỏng ngay lúc dựng request và trả 500 mang
    "api_key" và "credential", chứ không trả 401. Test ban đầu chỉ kiểm 401 nên đã xanh
    trong khi giảng viên thật nhận câu "hết số dư" cho một khóa trống."""
    monkeypatch.setattr(litellm, "completion", _nem(loi_litellm.InternalServerError(
        message="OpenAIException - The api_key client option must be set; missing credential",
        llm_provider="openai", model="gpt-4o-mini",
    )))

    with pytest.raises(catalog.ModelUnusable) as bat:
        catalog.probe("openai-fast")

    assert "api key" in bat.value.ly_do.lower()


def test_khoa_co_nhung_chua_nap_tien_thi_bao_het_so_du(monkeypatch):
    """Cũng đo trên gateway thật: khóa Anthropic có giá trị nhưng chưa nạp tiền thì ra 400
    mang "credit", "balance", "billing"."""
    monkeypatch.setattr(litellm, "completion", _nem(loi_litellm.BadRequestError(
        message="Your credit balance is too low. Please go to Plans & Billing",
        model="claude-haiku-4-5", llm_provider="anthropic",
    )))

    with pytest.raises(catalog.ModelUnusable) as bat:
        catalog.probe("claude-fast")

    assert "số dư" in bat.value.ly_do


def test_loi_khong_ro_nguyen_nhan_thi_khong_doan_bua(monkeypatch):
    """Đoán sai còn tệ hơn không nói: bảo "hết số dư" khi thực ra là thiếu khóa thì người ta
    đi nạp tiền. Không nhận ra nguyên nhân thì câu thông báo không được nêu nguyên nhân."""
    monkeypatch.setattr(litellm, "completion", _nem(loi_litellm.BadRequestError(
        message="something unexpected happened", model="x", llm_provider="openai",
    )))

    with pytest.raises(catalog.ModelUnusable) as bat:
        catalog.probe("openai-pro")

    ly_do = bat.value.ly_do.lower()
    assert "api key" not in ly_do and "số dư" not in ly_do and "hạn mức" not in ly_do


def test_gateway_khong_voi_toi_duoc_thi_la_gateway_sap_chu_khong_phai_model_hong(monkeypatch):
    """Phân biệt quan trọng: gateway sập thì mọi model đều hỏng và đổi model không giúp gì,
    nên phải báo 503 chứ không bảo giảng viên chọn model khác."""
    monkeypatch.setattr(litellm, "completion", _nem(
        loi_litellm.APIConnectionError(message="refused", llm_provider="openai", model="x")
    ))

    with pytest.raises(catalog.GatewayUnavailable):
        catalog.probe("groq-pro")


def test_gateway_qua_han_cho_cung_la_gateway_sap(monkeypatch):
    monkeypatch.setattr(litellm, "completion", _nem(
        loi_litellm.Timeout(message="timeout", model="x", llm_provider="openai")
    ))

    with pytest.raises(catalog.GatewayUnavailable):
        catalog.probe("groq-pro")
