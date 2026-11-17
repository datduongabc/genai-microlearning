"""Danh sách model mà giảng viên được chọn, đọc từ chính gateway.

Không ghi cứng danh sách trong mã: thêm một model là sửa devops/gateway/config.yaml,
và backend phải thấy ngay. Phần biến đổi được tách khỏi phần gọi mạng để kiểm được
trên dữ liệu tổng hợp phủ đủ các trường hợp, thay vì chỉ trên những gì gateway hiện
tại tình cờ trả về.
"""

import litellm
import litellm.exceptions as litellm_errors
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
        _muc("gemini-flash", "gemini/gemini-3.5-flash-lite"),
        _muc("embed-default", "gemini/gemini-embedding-001", mode="embedding"),
    ])

    assert [c.alias for c in chon] == ["gemini-flash"]



def test_model_khong_ro_che_do_thi_khong_dem_ra_chon():
    """Thà bỏ sót một model còn hơn đem một model nhúng ra cho chọn: bỏ sót thì giảng viên
    thấy thiếu và báo, còn chọn sai thì lỗi hiện ra ở chỗ khác hẳn."""
    chon = catalog.to_choices([_muc("la-lam", "abc/xyz", mode=None)])

    assert chon == []


def test_nhan_hien_thi_lay_tu_cau_hinh_gateway():
    chon = catalog.to_choices([
        _muc("gemini-flash", "gemini/gemini-3.5-flash-lite", display_name="Gemini Flash Lite")
    ])

    assert chon[0].label == "Gemini Flash Lite"


def test_thieu_nhan_thi_dung_chinh_alias_lam_nhan():
    """Thêm model ở gateway mà quên nhãn thì nó vẫn chọn được, chỉ là tên không đẹp."""
    chon = catalog.to_choices([_muc("groq-model", "groq/openai/gpt-oss-120b")])

    assert chon[0].label == "groq-model"


def test_ghi_chu_duoc_chuyen_tiep_de_giang_vien_biet_nen_chon_cai_nao():
    chon = catalog.to_choices([
        _muc("gemini-flash", "gemini/gemini-3.5-flash-lite", note="nhanh, miễn phí")
    ])

    assert chon[0].note == "nhanh, miễn phí"


def test_nha_cung_cap_suy_ra_tu_ten_model_chu_khong_phai_khai_them():
    """Suy ra từ tiền tố nên thêm model mới không phải khai lại nhà cung cấp. Groq đi qua
    litellm dưới dạng "groq/openai/..." nên phải lấy đúng đoạn đầu."""
    chon = catalog.to_choices([
        _muc("groq-model", "groq/openai/gpt-oss-120b"),
        _muc("claude-fast", "anthropic/claude-sonnet-5"),
    ])

    assert {c.alias: c.provider for c in chon} == {"groq-model": "groq", "claude-fast": "anthropic"}


def test_model_qua_openrouter_mang_ten_hang_lam_nha_cung_cap():
    chon = catalog.to_choices([
        _muc("claude-fast", "openrouter/anthropic/claude-haiku-5.5"),
        _muc("llama-model", "openrouter/meta-llama/llama-3.3-70b-instruct"),
    ])

    assert {c.alias: c.provider for c in chon} == {"claude-fast": "anthropic", "llama-model": "meta-llama"}


def test_thu_tu_on_dinh_de_danh_sach_tren_giao_dien_khong_nhay():
    xuoi = catalog.to_choices([
        _muc("openai-fast", "openai/gpt-4o-mini"),
        _muc("gemini-flash", "gemini/gemini-3.5-flash"),
        _muc("gemini-flash", "gemini/gemini-3.5-flash-lite"),
    ])
    nguoc = catalog.to_choices([
        _muc("gemini-flash", "gemini/gemini-3.5-flash-lite"),
        _muc("gemini-flash", "gemini/gemini-3.5-flash"),
        _muc("openai-fast", "openai/gpt-4o-mini"),
    ])

    assert [c.alias for c in xuoi] == [c.alias for c in nguoc]


def test_mot_alias_gateway_khong_phuc_vu_thi_khong_hop_le(monkeypatch):
    """Không tin client: gửi lên một alias lạ thì phải bị từ chối ở biên, chứ không để nó
    đi tới lệnh gọi LLM rồi hỏng ở đó."""
    monkeypatch.setattr(
        catalog, "available_models",
        lambda: catalog.to_choices([_muc("gemini-flash", "gemini/gemini-3.5-flash-lite")]),
    )

    assert catalog.is_available("gemini-flash")
    assert not catalog.is_available("embed-default")
    assert not catalog.is_available("khong-ton-tai")


def test_danh_sach_duoc_ghi_dem_de_moi_lan_mo_trang_khong_goi_lai_gateway(monkeypatch):
    so_lan = {"n": 0}

    def dem():
        so_lan["n"] += 1
        return [_muc("gemini-flash", "gemini/gemini-3.5-flash-lite")]

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
        return [_muc("gemini-flash", "gemini/gemini-3.5-flash-lite")]

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
    catalog.probe("deepseek-flash")


def test_thu_dung_model_da_chon_va_chi_xin_mot_token(ghi_lai_lenh_thu):
    """Một token là đủ để biết khóa và số dư còn dùng được, mà không tốn hạn mức đáng kể."""
    catalog.probe("deepseek-flash")

    assert ghi_lai_lenh_thu["model"] == "openai/deepseek-flash"
    assert ghi_lai_lenh_thu["max_tokens"] == 1
    assert ghi_lai_lenh_thu["extra_body"]["disable_fallbacks"] is True


def test_the_probe_gives_up_long_before_cloudfront_does(ghi_lai_lenh_thu):
    """The probe runs while the lecturer waits on "Sinh", and CloudFront answers 504 at 60 s."""
    catalog.probe("deepseek-flash")

    assert ghi_lai_lenh_thu["timeout"] <= 20


def test_khoa_trong_hoac_sai_thi_bao_chua_co_khoa(monkeypatch):
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.AuthenticationError(message="401", llm_provider="openai", model="gpt-4o")
    ))

    with pytest.raises(catalog.ModelUnusable) as raised:
        catalog.probe("openai-fast")

    assert raised.value.alias == "openai-fast"
    assert "api key" in raised.value.reason.lower()


def test_probe_429_is_usable_for_router_fallbacks(monkeypatch):
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.RateLimitError(message="429", llm_provider="gemini", model="gemini-flash")
    ))
    catalog.probe("gemini-flash")


def test_khoa_trong_that_ra_500_chu_khong_phai_401_van_bao_dung_la_thieu_khoa(monkeypatch):
    """Hình dạng lỗi đo trên gateway thật của stack này, không phải hình dạng người ta hay
    tưởng: khóa OpenAI trống thì gateway hỏng ngay lúc dựng request và trả 500 mang
    "api_key" và "credential", chứ không trả 401. Test ban đầu chỉ kiểm 401 nên đã xanh
    trong khi giảng viên thật nhận câu "hết số dư" cho một khóa trống."""
    monkeypatch.setattr(litellm, "completion", _nem(litellm_errors.InternalServerError(
        message="OpenAIException - The api_key client option must be set; missing credential",
        llm_provider="openai", model="gpt-4o-mini",
    )))

    with pytest.raises(catalog.ModelUnusable) as raised:
        catalog.probe("openai-fast")

    assert "api key" in raised.value.reason.lower()


def test_khoa_co_nhung_chua_nap_tien_thi_bao_het_so_du(monkeypatch):
    """Cũng đo trên gateway thật: khóa Anthropic có giá trị nhưng chưa nạp tiền thì ra 400
    mang "credit", "balance", "billing"."""
    monkeypatch.setattr(litellm, "completion", _nem(litellm_errors.BadRequestError(
        message="Your credit balance is too low. Please go to Plans & Billing",
        model="claude-haiku-4-5", llm_provider="anthropic",
    )))

    with pytest.raises(catalog.ModelUnusable) as raised:
        catalog.probe("claude-fast")

    assert "số dư" in raised.value.reason


def test_loi_khong_ro_nguyen_nhan_thi_khong_doan_bua(monkeypatch):
    """Đoán sai còn tệ hơn không nói: bảo "hết số dư" khi thực ra là thiếu khóa thì người ta
    đi nạp tiền. Không nhận ra nguyên nhân thì câu thông báo không được nêu nguyên nhân."""
    monkeypatch.setattr(litellm, "completion", _nem(litellm_errors.BadRequestError(
        message="something unexpected happened", model="x", llm_provider="openai",
    )))

    with pytest.raises(catalog.ModelUnusable) as raised:
        catalog.probe("openai-fast")

    reason = raised.value.reason.lower()
    assert "api key" not in reason and "số dư" not in reason and "hạn mức" not in reason


def test_gateway_khong_voi_toi_duoc_thi_la_gateway_sap_chu_khong_phai_model_hong(monkeypatch):
    """Phân biệt quan trọng: gateway sập thì mọi model đều hỏng và đổi model không giúp gì,
    nên phải báo 503 chứ không bảo giảng viên chọn model khác."""
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.APIConnectionError(message="refused", llm_provider="openai", model="x")
    ))

    with pytest.raises(catalog.GatewayUnavailable):
        catalog.probe("deepseek-flash")


def test_gateway_qua_han_cho_cung_la_gateway_sap(monkeypatch):
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.Timeout(message="timeout", model="x", llm_provider="openai")
    ))

    with pytest.raises(catalog.GatewayUnavailable):
        catalog.probe("deepseek-flash")


def test_hidden_aliases_are_skipped_and_an_alias_with_two_deployments_is_offered_once():
    raw = [
        {"model_name": alias, "litellm_params": {"model": model},
         "model_info": {"mode": "chat", "hidden": hidden}}
        for alias, model, hidden in (
            ("internal-step", "openrouter/google/gemini-3.8-flash", True),
            ("gemini-flash", "openrouter/google/gemini-3.8-flash", False),
            ("gemini-flash", "openrouter/google/gemini-3.8-flash", False),
        )
    ]
    assert [choice.alias for choice in catalog.to_choices(raw)] == ["gemini-flash"]


@pytest.mark.parametrize("error", [
    litellm_errors.AuthenticationError(message="invalid API key", llm_provider="anthropic", model="claude"),
    litellm_errors.BadRequestError(message="credit balance too low", llm_provider="anthropic", model="claude"),
])
def test_unusable_primary_is_rejected_with_422_without_probing_fallback(error, monkeypatch):
    from fastapi import HTTPException

    from app.api.modules import _usable_alias

    sent = []
    def completion(**kwargs):
        sent.append(kwargs)
        raise error
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(catalog, "is_available", lambda alias: True)
    with pytest.raises(HTTPException) as rejected:
        _usable_alias("claude-fast")
    assert rejected.value.status_code == 422
    assert len(sent) == 1 and sent[0]["model"] == "openai/claude-fast"
    assert sent[0]["extra_body"]["disable_fallbacks"] is True


def test_quota_exhausted_primary_still_passes_generation_alias_validation(monkeypatch):
    from app.api.modules import _usable_alias

    monkeypatch.setattr(catalog, "is_available", lambda alias: True)
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.RateLimitError(message="quota exhausted", llm_provider="gemini", model="gemini-flash")
    ))
    assert _usable_alias("gemini-flash") == "gemini-flash"


@pytest.mark.parametrize("message", [
    ("Error code: 429 - {'error': {'code': 'insufficient_quota', 'message': 'You exceeded your "
     "current quota, please check your plan and billing details.'}}"),
    "billing_hard_limit_reached",
    "Not enough credit",
    "Insufficient funds",
])
def test_credit_429_rejects_model_selection_with_422(message, monkeypatch):
    from fastapi import HTTPException

    from app.api.modules import _usable_alias

    monkeypatch.setattr(catalog, "is_available", lambda alias: True)
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.RateLimitError(message=message, llm_provider="openai", model="gpt-4o-mini")
    ))
    with pytest.raises(HTTPException) as rejected:
        _usable_alias("openai-fast")
    assert rejected.value.status_code == 422
    assert "số dư" in rejected.value.detail


@pytest.mark.parametrize("message", [
    "RESOURCE_EXHAUSTED: quota exhausted. Received Model Group=gemini-flash",
    ("GeminiException - {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'message': 'You "
     "exceeded your current quota, please check your plan and billing details. For more "
     "information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits'}}"),
    "No deployments available. Passed model=gemini-flash",
])
def test_a_quota_429_does_not_mean_insufficient_balance(message, monkeypatch):
    monkeypatch.setattr(litellm, "completion", _nem(
        litellm_errors.RateLimitError(message=message, llm_provider="gemini", model="gemini-flash")
    ))
    catalog.probe("gemini-flash")


def test_gemini_resource_exhausted_is_accepted_and_not_reported_as_missing_credit(monkeypatch):
    from app.api.modules import _usable_alias

    loi = litellm_errors.RateLimitError(
        message="{'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'message': 'You exceeded "
        "your current quota, please check your plan and billing details.'}}",
        llm_provider="gemini", model="gemini-flash",
    )
    monkeypatch.setattr(catalog, "is_available", lambda alias: True)
    monkeypatch.setattr(litellm, "completion", _nem(loi))
    assert _usable_alias("gemini-flash") == "gemini-flash"
    assert "số dư" not in catalog._reason(loi)
