"""Giảng viên chọn model qua API: xem được những model nào, và đặt được mặc định của mình.

Gateway được thay bằng một danh sách cố định, vì đây là test về API chứ không phải về
gateway, và bộ test không được phụ thuộc vào việc gateway có đang chạy hay không.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.llm import catalog
from app.main import app
from tests.auth_helpers import STRONG_PASSWORD, make_account

client = TestClient(app)

GATEWAY_GIA_LAP = [
    {
        "model_name": "gemini-fast",
        "litellm_params": {"model": "gemini/gemini-3.5-flash-lite"},
        "model_info": {"mode": "chat", "display_name": "Gemini Flash Lite", "note": "nhanh, miễn phí"},
    },
    {
        "model_name": "groq-pro",
        "litellm_params": {"model": "groq/openai/gpt-oss-120b"},
        "model_info": {"mode": "chat"},
    },
    {
        "model_name": "embed-default",
        "litellm_params": {"model": "gemini/gemini-embedding-001"},
        "model_info": {"mode": "embedding"},
    },
]


da_thu: list[str] = []


@pytest.fixture(autouse=True)
def gateway_gia_lap(monkeypatch):
    monkeypatch.setattr(catalog, "fetch_model_info", lambda: GATEWAY_GIA_LAP)
    # Phép thử thật sẽ gọi gateway đang chạy trên máy và tốn hạn mức. Mặc định là thử được;
    # test nào cần model hỏng thì tự đặt lại. Ghi lại alias đã thử để kiểm được khi nào có thử.
    da_thu.clear()
    monkeypatch.setattr(catalog, "probe", lambda alias: da_thu.append(alias))
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def _dang_nhap() -> dict:
    email = f"model-{uuid.uuid4().hex[:10]}@example.com"
    make_account(email, name="Test")
    token = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_khach_chua_dang_nhap_khong_xem_duoc_danh_sach():
    assert client.get("/models").status_code == 401


def test_danh_sach_chi_gom_model_sinh_van_ban_kem_nhan_va_nha_cung_cap():
    body = client.get("/models", headers=_dang_nhap()).json()

    assert [m["alias"] for m in body["models"]] == ["gemini-fast", "groq-pro"]
    assert body["models"][0]["label"] == "Gemini Flash Lite"
    assert body["models"][0]["provider"] == "gemini"
    assert body["models"][0]["note"] == "nhanh, miễn phí"


def test_thieu_nhan_thi_tra_ve_chinh_alias():
    body = client.get("/models", headers=_dang_nhap()).json()
    groq = next(m for m in body["models"] if m["alias"] == "groq-pro")

    assert groq["label"] == "groq-pro"


def test_giang_vien_moi_thi_chua_co_mac_dinh():
    body = client.get("/models", headers=_dang_nhap()).json()

    assert body["default"] is None


def test_dat_mac_dinh_roi_xem_lai_thi_thay():
    headers = _dang_nhap()

    dat = client.put("/models/default", json={"alias": "groq-pro"}, headers=headers)

    assert dat.status_code == 200
    assert client.get("/models", headers=headers).json()["default"] == "groq-pro"


def test_xoa_mac_dinh_bang_cach_dat_ve_null():
    headers = _dang_nhap()
    client.put("/models/default", json={"alias": "groq-pro"}, headers=headers)

    client.put("/models/default", json={"alias": None}, headers=headers)

    assert client.get("/models", headers=headers).json()["default"] is None


def test_alias_gateway_khong_phuc_vu_thi_bi_tu_choi():
    """Không tin client. Nhận một alias lạ rồi lưu vào database thì mọi lượt sinh sau đó
    của giảng viên này hỏng, mà lỗi hiện ra ở tận lệnh gọi LLM."""
    headers = _dang_nhap()

    phan_hoi = client.put("/models/default", json={"alias": "khong-ton-tai"}, headers=headers)

    assert phan_hoi.status_code == 422
    assert client.get("/models", headers=headers).json()["default"] is None


def test_khong_cho_chon_model_nhung_lam_mac_dinh_sinh_noi_dung():
    headers = _dang_nhap()

    phan_hoi = client.put("/models/default", json={"alias": "embed-default"}, headers=headers)

    assert phan_hoi.status_code == 422


def test_mac_dinh_cua_nguoi_nay_khong_anh_huong_nguoi_khac():
    mot = _dang_nhap()
    hai = _dang_nhap()

    client.put("/models/default", json={"alias": "groq-pro"}, headers=mot)

    assert client.get("/models", headers=hai).json()["default"] is None


def test_gateway_khong_tra_loi_thi_bao_503_chu_khong_phai_500(monkeypatch):
    """500 kèm traceback đọc như một lỗi của backend, trong khi thực ra backend vẫn ổn và
    chỉ là gateway chưa lên. 503 nói đúng điều đó, và giao diện biết nên bảo thử lại."""
    import httpx

    def gateway_sap():
        raise httpx.ConnectError("gateway khong tra loi")

    monkeypatch.setattr(catalog, "fetch_model_info", gateway_sap)
    catalog.clear_cache()

    phan_hoi = client.get("/models", headers=_dang_nhap())

    assert phan_hoi.status_code == 503
    assert "gateway" in phan_hoi.json()["detail"].lower()


def test_gateway_tra_ma_loi_thi_cung_bao_503(monkeypatch):
    import httpx

    def gateway_loi():
        request = httpx.Request("GET", "http://gateway.test/model/info")
        raise httpx.HTTPStatusError("loi", request=request, response=httpx.Response(500, request=request))

    monkeypatch.setattr(catalog, "fetch_model_info", gateway_loi)
    catalog.clear_cache()

    assert client.get("/models", headers=_dang_nhap()).status_code == 503


def test_luu_mac_dinh_thi_model_duoc_thu_truoc():
    """Mặc định được dùng cho mọi lượt sinh về sau, nên lưu một model chưa dùng được làm mặc
    định là để dành một lỗi cho mọi lần bấm sinh tiếp theo. Báo ngay lúc lưu thì hơn."""
    client.put("/models/default", json={"alias": "groq-pro"}, headers=_dang_nhap())

    assert da_thu == ["groq-pro"]


def test_model_chua_dung_duoc_thi_khong_luu_lam_mac_dinh(monkeypatch):
    def model_hong(alias):
        raise catalog.ModelUnusable(alias, "chưa có API key hợp lệ cho nhà cung cấp của model này")

    monkeypatch.setattr(catalog, "probe", model_hong)
    headers = _dang_nhap()

    phan_hoi = client.put("/models/default", json={"alias": "groq-pro"}, headers=headers)

    assert phan_hoi.status_code == 422
    assert "api key" in phan_hoi.json()["detail"].lower()
    assert client.get("/models", headers=headers).json()["default"] is None


def test_xoa_mac_dinh_thi_khong_thu_gi():
    """Xoá mặc định là quay về cấu hình hệ thống, không có model nào để thử."""
    client.put("/models/default", json={"alias": None}, headers=_dang_nhap())

    assert da_thu == []
