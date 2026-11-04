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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from benchmark_rerank import _chi_so, _giay_phai_cho, _kiem_dinh, _mcnemar


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


def test_mcnemar_chi_dem_cap_cau_hai_ben_khac_nhau():
    # 10 câu chỉ bên sau đúng, 2 câu chỉ bên trước đúng, 3 câu cả hai cùng đúng và 1 câu
    # cả hai cùng sai. p chính xác hai phía = 2 * (C(12,0) + C(12,1) + C(12,2)) / 2^12
    # = 158 / 4096, đúng con số 0,039 của môn operating-system trong 2026-09-24-rerank.md.
    truoc = [False] * 10 + [True] * 2 + [True] * 3 + [False]
    sau = [True] * 10 + [False] * 2 + [True] * 3 + [False]

    kq = _mcnemar(truoc, sau)

    assert kq["chi_sau_dung"] == 10
    assert kq["chi_truoc_dung"] == 2
    assert kq["p"] == pytest.approx(158 / 4096)


def test_mcnemar_khong_co_cap_nao_lech_thi_p_bang_mot():
    assert _mcnemar([True, False], [True, False])["p"] == 1.0


def test_kiem_dinh_so_adaptive_k_voi_hybrid_lay_co_dinh_tam_chunk():
    # Câu đầu: hybrid để chunk đúng ở hạng 9, ngoài 8 chunk cố định, còn Adaptive-k có.
    # Câu sau: hybrid ở hạng 2, Adaptive-k cũng có.
    chi_tiet = [
        {"course": "a", "thu_hang": {"dense": 9, "hybrid": 9, "hybrid+rerank": 1},
         "adaptive_k": {"k": 12, "trung": True}},
        {"course": "a", "thu_hang": {"dense": 1, "hybrid": 2, "hybrid+rerank": 3},
         "adaptive_k": {"k": 8, "trung": True}},
    ]

    kq = _kiem_dinh(chi_tiet)["chung"]["adaptive-k so với hybrid@8"]

    assert (kq["chi_sau_dung"], kq["chi_truoc_dung"]) == (1, 0)
    assert kq["k_trung_binh"] == 10


def test_doc_thoi_gian_cho_tu_loi_tu_choi_cua_groq():
    assert _giay_phai_cho("Please try again in 1m2.5s. Need more tokens?") == pytest.approx(63.5)
    assert _giay_phai_cho("Please try again in 7.2s.") == pytest.approx(8.2)


def test_partial_run_drops_old_subjects_from_aggregate(tmp_path, monkeypatch):
    """A rerun of one subject must not carry excluded historical subjects into the report."""
    import json
    from types import SimpleNamespace

    import benchmark_rerank as benchmark

    monkeypatch.setattr(benchmark, 'THU_MUC_KET_QUA', tmp_path)
    (tmp_path / benchmark.TEP_TRUY_VAN).write_text('[]')
    old = [
        {'course': course, 'thu_hang': {name: rank for name in benchmark.CAU_HINH}}
        for course, rank in [('operating-system', 1), ('dsa', None), ('chemistry1', None)]
    ]
    (tmp_path / benchmark.TEP_THU_HANG).write_text(json.dumps(old))
    db = SimpleNamespace(query=lambda model: SimpleNamespace(all=list))
    benchmark.danh_gia(db, ['calculus 1'])
    result = json.loads((tmp_path / benchmark.TEP_KET_QUA).read_text())
    assert result['dense']['chung']['recall@1'] == 1
    assert set(result['dense']['tung_mon']) == {'operating-system'}
    assert {row['course'] for row in json.loads((tmp_path / benchmark.TEP_THU_HANG).read_text())} == {'operating-system'}
