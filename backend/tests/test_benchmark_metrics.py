"""Công thức tính chỉ số của phép đo truy hồi.

Một lỗi ở đây không làm chương trình hỏng, nó chỉ làm mọi kết luận rút ra từ phép
đo sai đi mà bảng số vẫn trông bình thường. Các giá trị mong đợi dưới đây tính tay
từ định nghĩa của từng chỉ số, không đọc từ cách hàm đang cài đặt.
"""

import math
import sys
from pathlib import Path

import pytest

# scripts/ không phải package nên phải chỉ đường trước khi nhập.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_rerank import _chi_so


def test_tat_ca_dung_o_vi_tri_dau_thi_moi_chi_so_deu_tuyet_doi():
    chi_so = _chi_so([1, 1, 1])

    assert chi_so["recall@1"] == 1.0
    assert chi_so["mrr@10"] == 1.0
    assert chi_so["ndcg@10"] == 1.0


def test_khong_truy_van_nao_tim_thay_thi_moi_chi_so_deu_bang_khong():
    chi_so = _chi_so([None, None])

    assert chi_so["recall@1"] == 0.0
    assert chi_so["recall@10"] == 0.0
    assert chi_so["mrr@10"] == 0.0
    assert chi_so["ndcg@10"] == 0.0


def test_recall_dem_ty_le_truy_van_co_chunk_dung_nam_trong_top_k():
    # Thứ hạng 1, 4, 9 và một lần trượt hẳn: trong 4 truy vấn thì top-3 bắt được 1,
    # top-5 bắt được 2, top-10 bắt được 3.
    chi_so = _chi_so([1, 4, 9, None])

    assert chi_so["recall@1"] == 0.25
    assert chi_so["recall@3"] == 0.25
    assert chi_so["recall@5"] == 0.5
    assert chi_so["recall@10"] == 0.75


def test_mrr_la_trung_binh_cua_nghich_dao_thu_hang():
    chi_so = _chi_so([1, 2])

    assert chi_so["mrr@10"] == pytest.approx((1 / 1 + 1 / 2) / 2)


def test_truy_van_truot_duoc_tinh_la_khong_chu_khong_bi_bo_khoi_mau_so():
    """Bỏ câu trượt khỏi mẫu số sẽ thổi phồng chỉ số: một hệ thống chỉ trả lời được
    1 trong 100 câu nhưng trả đúng vị trí đầu sẽ có MRR bằng 1,0."""
    chi_so = _chi_so([1, None])

    assert chi_so["mrr@10"] == 0.5
    assert chi_so["ndcg@10"] == 0.5


def test_ndcg_giam_theo_logarit_cua_thu_hang():
    # Mức liên quan nhị phân, chỉ có một chunk đúng: DCG = 1/log2(hạng+1), và IDCG
    # bằng 1 vì lý tưởng là chunk đúng nằm ở hạng 1.
    chi_so = _chi_so([3])

    assert chi_so["ndcg@10"] == pytest.approx(1 / math.log2(4))


def test_ndcg_luon_nam_giua_mrr_va_recall_khi_thu_hang_lon_hon_mot():
    """Phạt của nDCG nhẹ hơn của MRR: tụt từ hạng 1 xuống hạng 4 làm MRR còn một
    phần tư, còn nDCG vẫn giữ lại quá nửa."""
    chi_so = _chi_so([4])

    assert chi_so["mrr@10"] == 0.25
    assert chi_so["ndcg@10"] > chi_so["mrr@10"]
    assert chi_so["ndcg@10"] < chi_so["recall@10"]
