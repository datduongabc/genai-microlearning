"""Đo xem tầng rerank có đáng giữ không.

Câu hỏi cần trả lời: cross-encoder (nay chạy ở service reranker
riêng, xem devops/docker-compose.yml) cải thiện truy hồi bao nhiêu so với hybrid
search không rerank, và so với dense search đơn thuần?

Nhãn đúng sinh tự động: lấy một chunk, nhờ LLM đặt câu hỏi mà chỉ đoạn đó trả lời
được, rồi đo xem truy hồi có kéo đúng chunk ấy về không. Prompt sinh câu hỏi KHÔNG
chứa ví dụ mẫu nào, vì một ví dụ lấy từ chính tài liệu đang đo là mớm đáp án và sẽ
cho số đẹp giả.

Chạy ba pha riêng vì hạn mức nhúng của nhà cung cấp tính theo từng văn bản và giới
hạn theo ngày: hỏng ở pha sau mà phải nhúng lại từ đầu là mất hạn mức cả ngày.

    uv run python scripts/benchmark_rerank.py nap
    uv run python scripts/benchmark_rerank.py sinh-truy-van
    uv run python scripts/benchmark_rerank.py danh-gia

Thêm --mon để chạy lại một môn mà không đụng vào hai môn kia; kết quả của các môn
không chạy được giữ nguyên và bảng tổng tính lại từ toàn bộ thứ hạng đã lưu.

Pha sinh truy vấn ghi tệp sau từng câu và chạy lại thì chỉ hỏi những chunk chưa có
câu hỏi, để lỗi giữa chừng không bỏ phí phần đã sinh. groq-pro nay chạy trên gói trả
phí của Groq nên mỗi lần sinh đều bị tính tiền (vài cent cho một benchmark); nếu Groq
từ chối vì hết hạn mức thì script dừng, chạy lại đúng lệnh cũ để làm tiếp. Muốn sinh
lại từ đầu thì xoá tệp truy vấn.

PHẢI chạy bên trong container backend, không chạy trên máy thật: tesseract chỉ có
trong ảnh, mà thiếu nó thì các trang PDF không có lớp văn bản sẽ rỗng và corpus
khuyết nội dung một cách lặng lẽ. Lần đo đầu tiên đã hỏng đúng vì lý do này.

Biến môi trường:
  BENCHMARK_DATABASE_URL  database riêng, không dùng chung với database bộ test
  BENCHMARK_DOCUMENTS     thư mục tài liệu (trong container nằm ngoài /app)
  BENCHMARK_KET_QUA       thư mục ghi kết quả
  RERANKER_URL            service reranker (trong stack compose: http://reranker:80)
"""

import argparse
import json
import math
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path

import litellm
from litellm.exceptions import RateLimitError
from scipy import stats
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.chunker import chunk_units, document_title
from app.ingestion.document_readers import read_document
from app.ingestion.embeddings import embed_texts
from app.models.models import Chunk, Course, Document, DocumentKind, DocumentStatus, User
from app.retrieval.search import dense_search, hybrid_search, invalidate_sparse_cache, retrieve

GOC = Path(__file__).resolve().parents[2]
# Trong container chỉ có backend/ được gắn vào, tài liệu và thư mục kết quả nằm
# ngoài cây đó nên phải chỉ đường bằng biến môi trường.
GOC_TAI_LIEU = Path(os.environ.get("BENCHMARK_DOCUMENTS") or GOC / "docs" / "documents")
THU_MUC_KET_QUA = Path(os.environ.get("BENCHMARK_KET_QUA") or GOC / "docs" / "measurements")

# Số tệp đầu tiên theo thứ tự bảng chữ cái của mỗi môn, lấy theo thứ tự cố định để lặp lại
# được. Ba môn tiếng Anh cắt bớt cho vừa hạn mức nhúng một ngày. Hai môn tiếng Việt thêm sau
# (nạp riêng bằng --mon) vì bge-reranker-base thiên về tiếng Anh, mà hệ thống dành cho giảng
# viên Việt Nam: đo trên tiếng Anh thì chưa đủ để quyết giữ hay đổi reranker.
MON = {
    "dsa": 5, "operating-system": 7, "advanced-programming": 11,
    "program-fundamentals": 7, "web-programming": 7,
    "calculus1": 8, "chemistry1": 9,
}

TRUY_VAN_MOI_MON = 50
# Chunk quá ngắn không đủ chi tiết để đặt một câu hỏi chỉ nó trả lời được, nên
# câu hỏi sinh ra sẽ mơ hồ và nhãn đúng trở nên vô nghĩa.
TOI_THIEU_KY_TU = 300
HAT_GIONG = 20260923

MO_HINH_SINH_CAU_HOI = "groq-pro"
SO_CHUNK_LAY_VE = 10
CAC_MUC_K = (1, 3, 5, 8, 10)

CAU_HINH = ("dense", "hybrid", "hybrid+rerank")

# Bộ câu hỏi của lần đo 2026-09-26, nhúng bằng Qwen3. Bộ cũ (truy_van.json, thu_hang.json,
# ket_qua.json) giữ nguyên làm lịch sử: đáp án của nó là chunk trong một database đã mất,
# nên không chấm lại được trên chunk mới.
TEP_TRUY_VAN = "truy_van_qwen3.json"
TEP_THU_HANG = "thu_hang_qwen3.json"
TEP_KET_QUA = "ket_qua_qwen3.json"

# groq-pro (gpt-oss-120b) nay ở gói trả phí, trần thật cao hơn nhiều. 8.000 token mỗi phút
# là nhịp cố ý giữ chậm để chặn chi phí, không phải hạn mức gói miễn phí: chờ sau mỗi câu
# cho đủ phần token vừa dùng theo nhịp này.
TOKEN_MOI_PHUT = 8000

# LO và Content gọi retrieve(top_k=None, min_k=8); nếu bỏ Adaptive-k thì chúng lấy cố định
# chừng ấy chunk từ hybrid. Hai tập này được so với nhau.
ADAPTIVE_MIN_K = 8

NHAC_VIEC = (
    "Bạn nhận một đoạn trích từ tài liệu giảng dạy. Hãy viết MỘT câu hỏi mà người đọc "
    "chỉ trả lời được nếu đã đọc đúng đoạn trích này.\n"
    "Yêu cầu:\n"
    "- Câu hỏi phải cụ thể, nhắc tới thuật ngữ hoặc chi tiết riêng của đoạn trích.\n"
    "- Viết như một người học tự đặt câu hỏi, chưa hề nhìn thấy đoạn trích.\n"
    "- Cấm mọi cách nhắc tới đoạn trích, kể cả \"đoạn này\", \"theo đoạn trích\",\n"
    "  \"according to the excerpt\", \"in the code above\".\n"
    "- Câu hỏi phải đứng một mình vẫn hiểu được, không phụ thuộc thứ tự ví dụ trong bài.\n"
    # Trộn ngôn ngữ làm hỏng phép so sánh: truy vấn khác ngôn ngữ với văn bản thì BM25
    # mất sạch tín hiệu trùng từ, còn dense thì không, nên nhánh sparse bị phạt oan.
    "- BẮT BUỘC viết câu hỏi bằng CHÍNH ngôn ngữ của đoạn trích. Đoạn trích tiếng Anh "
    "thì câu hỏi phải tiếng Anh, đoạn trích tiếng Việt thì câu hỏi tiếng Việt.\n"
    "- Chỉ trả về câu hỏi, không thêm gì khác.\n\n"
    "Đoạn trích:\n<<<\n{noi_dung}\n>>>"
)


def _phien() -> Session:
    url = os.environ.get("BENCHMARK_DATABASE_URL")
    if not url:
        sys.exit("thiếu BENCHMARK_DATABASE_URL")
    return Session(create_engine(url, future=True))


def _tep_ket_qua(ten: str) -> Path:
    THU_MUC_KET_QUA.mkdir(parents=True, exist_ok=True)
    return THU_MUC_KET_QUA / ten


def _cac_tep(mon: str) -> list[Path]:
    tep = sorted(p for p in (GOC_TAI_LIEU / mon).iterdir() if p.is_file())
    return tep[: MON[mon]]


# --- pha 1: nạp tài liệu -----------------------------------------------------


def nap(db: Session, cac_mon: list[str]) -> None:
    chu = db.query(User).first()
    if chu is None:
        chu = User(
            id=uuid.uuid4(), email="benchmark@example.com", name="Benchmark",
            hashed_password="khong-dung-de-dang-nhap", is_admin=False,
        )
        db.add(chu)
        db.flush()

    tong_chunk = 0
    for mon in cac_mon:
        # Nạp lại một môn đã có là nhúng lại toàn bộ chunk của nó, tốn hạn mức vô ích.
        # Muốn nạp lại thì xoá course đó trước.
        if db.query(Course).filter(Course.name == mon).count():
            print(f"=== bỏ qua {mon}: đã có trong database")
            continue
        khoa = Course(id=uuid.uuid4(), name=mon, owner_id=chu.id)
        db.add(khoa)
        db.flush()
        print(f"\n=== {mon}")

        for tep in _cac_tep(mon):
            units = read_document(str(tep), course_id=str(khoa.id), source_id=None)
            cac_chunk = chunk_units(units, title=document_title(tep.name))
            tai_lieu = Document(
                id=uuid.uuid4(), course_id=khoa.id, filename=tep.name,
                status=DocumentStatus.embedded, kind=DocumentKind.lecture,
            )
            db.add(tai_lieu)
            db.flush()

            vectors = embed_texts([c.content for c in cac_chunk])
            for chi_so, (c, vector) in enumerate(zip(cac_chunk, vectors, strict=True)):
                db.add(Chunk(
                    id=uuid.uuid4(), document_id=tai_lieu.id, course_id=khoa.id,
                    chunk_index=chi_so, content=c.content,
                    # Giống hệt _run_embed của document_pipeline: locator tuần tự hoá thành JSON.
                    source_ref=json.dumps(c.locator, ensure_ascii=False),
                    embedding=vector,
                ))
            db.commit()
            tong_chunk += len(cac_chunk)
            print(f"  {tep.name[:52]:<52} {len(cac_chunk):>4} chunk")

    print(f"\n=== đã nhúng {tong_chunk} chunk")


# --- pha 2: sinh truy vấn ----------------------------------------------------


class HetHanMucNgay(Exception):
    pass


def _giay_phai_cho(thong_bao: str) -> float:
    """Groq ghi thời gian chờ vào lời từ chối, dạng "try again in 1m2.5s"."""
    khop = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", thong_bao)
    if not khop:
        return 30.0
    return int(khop.group(1) or 0) * 60 + float(khop.group(2)) + 1


def _hoi_llm(noi_dung: str) -> str:
    while True:
        try:
            tra_loi = litellm.completion(
                model=f"openai/{MO_HINH_SINH_CAU_HOI}",
                api_base=settings.llm_gateway_url,
                api_key=settings.llm_gateway_key,
                messages=[{"role": "user", "content": NHAC_VIEC.format(noi_dung=noi_dung)}],
                temperature=0.3,
            )
            break
        except RateLimitError as loi:
            if "per day" in str(loi):
                raise HetHanMucNgay(str(loi)) from loi
            time.sleep(_giay_phai_cho(str(loi)))
    so_token = (tra_loi.get("usage") or {}).get("total_tokens") or 0
    time.sleep(so_token / TOKEN_MOI_PHUT * 60)
    return tra_loi["choices"][0]["message"]["content"].strip()


def sinh_truy_van(db: Session, cac_mon: list[str]) -> None:
    rng = random.Random(HAT_GIONG)
    tep = _tep_ket_qua(TEP_TRUY_VAN)
    cu = json.loads(tep.read_text(encoding="utf-8")) if tep.exists() else []
    # Giữ truy vấn của những môn không chạy lần này, để chạy lại một môn không xoá mất
    # công sinh câu hỏi của các môn kia.
    truy_van = [t for t in cu if t["course"] not in cac_mon]

    def ghi() -> None:
        tep.write_text(json.dumps(truy_van, ensure_ascii=False, indent=2), encoding="utf-8")

    for khoa in db.query(Course).filter(Course.name.in_(cac_mon)).order_by(Course.name).all():
        ung_vien = [
            c for c in db.query(Chunk).filter(Chunk.course_id == khoa.id).order_by(Chunk.id).all()
            if len(c.content) >= TOI_THIEU_KY_TU
        ]
        chon = rng.sample(ung_vien, min(TRUY_VAN_MOI_MON, len(ung_vien)))
        # Câu hỏi đã sinh ở lần chạy trước cho đúng chunk này được dùng lại, không hỏi
        # lại tốn hạn mức. Câu của chunk không còn được chọn (database đã nạp lại) bị bỏ.
        da_co = {t["chunk_id"]: t for t in cu if t["course"] == khoa.name}
        print(f"\n=== {khoa.name}: {len(ung_vien)} chunk đủ dài, lấy {len(chon)}")
        for c in chon:
            if str(c.id) in da_co:
                truy_van.append(da_co[str(c.id)])
                continue
            try:
                cau_hoi = _hoi_llm(c.content)
            except HetHanMucNgay as loi:
                ghi()
                sys.exit(f"hết hạn mức ngày, đã lưu {len(truy_van)} câu; chạy lại sau: {loi}")
            # Bỏ qua một câu hỏi sinh lỗi, không để hỏng cả lượt chạy đã tốn hạn mức.
            except Exception as loi:
                print(f"  bỏ chunk {c.id}: {type(loi).__name__}: {loi}")
                continue
            truy_van.append({
                "course_id": str(khoa.id), "course": khoa.name,
                "chunk_id": str(c.id), "cau_hoi": cau_hoi,
            })
            ghi()
        print(f"  đã có {len([t for t in truy_van if t['course'] == khoa.name])} câu hỏi")

    ghi()
    print(f"\n=== tổng {len(truy_van)} truy vấn trong tệp")


# --- pha 3: đánh giá ---------------------------------------------------------


def _xep_hang(db: Session, course_id: uuid.UUID, cau_hoi: str, cau_hinh: str) -> list[uuid.UUID]:
    if cau_hinh == "dense":
        cac_chunk = dense_search(db, course_id, cau_hoi, top_k=SO_CHUNK_LAY_VE)
    elif cau_hinh == "hybrid":
        cac_chunk = hybrid_search(db, course_id, cau_hoi, top_k=SO_CHUNK_LAY_VE, candidate_k=30)
    else:
        cac_chunk = retrieve(db, course_id, cau_hoi, top_k=SO_CHUNK_LAY_VE, candidate_k=30)
    return [c.id for c in cac_chunk]


def _adaptive_k(db: Session, course_id: uuid.UUID, cau_hoi: str, dung: uuid.UUID) -> dict:
    """Tập chunk LO và Content thật sự nhận, so với tập cố định sẽ thay nó nếu bỏ Adaptive-k."""
    chon = [c.id for c in retrieve(db, course_id, cau_hoi, top_k=None, min_k=ADAPTIVE_MIN_K)]
    return {"k": len(chon), "trung": dung in chon}


def _mcnemar(truoc: list[bool], sau: list[bool]) -> dict:
    """McNemar chính xác hai phía: chỉ những cặp câu mà hai bên khác nhau mới mang
    thông tin, và dưới giả thuyết không khác biệt thì mỗi bên thắng một nửa số đó."""
    chi_sau = sum(1 for a, b in zip(truoc, sau, strict=True) if b and not a)
    chi_truoc = sum(1 for a, b in zip(truoc, sau, strict=True) if a and not b)
    lech = chi_sau + chi_truoc
    p = float(stats.binomtest(chi_sau, lech, 0.5).pvalue) if lech else 1.0
    return {"chi_sau_dung": chi_sau, "chi_truoc_dung": chi_truoc, "p": p}


def _trung(thu_hang: int | None, k: int) -> bool:
    return thu_hang is not None and thu_hang <= k


def _kiem_dinh(chi_tiet: list[dict]) -> dict:
    """Các phép kiểm của quy tắc quyết định trong 2026-09-26-rerank-qwen3.md, trên gộp
    mọi môn và trên từng môn."""
    nhom = {"chung": chi_tiet}
    for d in chi_tiet:
        nhom.setdefault(d["course"], []).append(d)
    ket_qua = {}
    for ten, ds in nhom.items():
        hang = {ch: [d["thu_hang"][ch] for d in ds] for ch in CAU_HINH}
        ket_qua[ten] = {
            f"{sau} so với {truoc}, recall@{k}": _mcnemar(
                [_trung(h, k) for h in hang[truoc]], [_trung(h, k) for h in hang[sau]]
            )
            for truoc, sau in (("hybrid", "hybrid+rerank"), ("dense", "hybrid"), ("dense", "hybrid+rerank"))
            for k in (1, 10)
        }
        if all("adaptive_k" in d for d in ds):
            ket_qua[ten][f"adaptive-k so với hybrid@{ADAPTIVE_MIN_K}"] = {
                **_mcnemar(
                    [_trung(h, ADAPTIVE_MIN_K) for h in hang["hybrid"]],
                    [d["adaptive_k"]["trung"] for d in ds],
                ),
                "k_trung_binh": sum(d["adaptive_k"]["k"] for d in ds) / len(ds),
            }
    return ket_qua


def _chi_so(cac_thu_hang: list[int | None]) -> dict:
    n = len(cac_thu_hang)
    ket_qua = {f"recall@{k}": sum(1 for r in cac_thu_hang if r is not None and r <= k) / n for k in CAC_MUC_K}
    ket_qua["mrr@10"] = sum(1 / r for r in cac_thu_hang if r is not None) / n
    ket_qua["ndcg@10"] = sum(1 / math.log2(r + 1) for r in cac_thu_hang if r is not None) / n
    return ket_qua


def danh_gia(db: Session, cac_mon: list[str]) -> None:
    truy_van = [
        t for t in json.loads(_tep_ket_qua(TEP_TRUY_VAN).read_text(encoding="utf-8"))
        if t["course"] in cac_mon
    ]
    for khoa in db.query(Course).all():
        invalidate_sparse_cache(khoa.id)

    # Thứ hạng từng câu được lưu lại, vì bảng chỉ số gộp không cho biết rerank đã
    # đẩy chunk đúng xuống ở những câu nào -- mà đó mới là thứ cần để tìm nguyên nhân.
    tep_chi_tiet = _tep_ket_qua(TEP_THU_HANG)
    cu = json.loads(tep_chi_tiet.read_text(encoding="utf-8")) if tep_chi_tiet.exists() else []
    chi_tiet = [d for d in cu if d["course"] not in cac_mon]

    for chi_so, t in enumerate(truy_van, start=1):
        course_id = uuid.UUID(t["course_id"])
        dung = uuid.UUID(t["chunk_id"])
        thu_hang = {}
        for cau_hinh in CAU_HINH:
            ids = _xep_hang(db, course_id, t["cau_hoi"], cau_hinh)
            thu_hang[cau_hinh] = ids.index(dung) + 1 if dung in ids else None
        chi_tiet.append({
            "course": t["course"], "chunk_id": t["chunk_id"],
            "cau_hoi": t["cau_hoi"], "thu_hang": thu_hang,
            "adaptive_k": _adaptive_k(db, course_id, t["cau_hoi"], dung),
        })
        if chi_so % 10 == 0:
            print(f"  {chi_so}/{len(truy_van)}", flush=True)

    tep_chi_tiet.write_text(json.dumps(chi_tiet, ensure_ascii=False, indent=2), encoding="utf-8")

    ket_qua = {}
    for cau_hinh in CAU_HINH:
        theo_mon: dict[str, list[int | None]] = {}
        for d in chi_tiet:
            theo_mon.setdefault(d["course"], []).append(d["thu_hang"][cau_hinh])
        ket_qua[cau_hinh] = {
            "tung_mon": {mon: _chi_so(hs) for mon, hs in sorted(theo_mon.items())},
            "chung": _chi_so([h for hs in theo_mon.values() for h in hs]),
        }
    ket_qua["kiem_dinh"] = _kiem_dinh(chi_tiet)

    _tep_ket_qua(TEP_KET_QUA).write_text(
        json.dumps(ket_qua, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _in_bang(ket_qua, len(chi_tiet))


def _in_bang(ket_qua: dict, so_truy_van: int) -> None:
    cot = [f"recall@{k}" for k in CAC_MUC_K] + ["mrr@10", "ndcg@10"]
    so_mon = len(ket_qua[CAU_HINH[0]]["tung_mon"])
    print(f"\n=== {so_truy_van} truy vấn, gộp {so_mon} môn")
    print("  " + "cấu hình".ljust(16) + "".join(c.rjust(11) for c in cot))
    for cau_hinh in CAU_HINH:
        hang = ket_qua[cau_hinh]["chung"]
        print("  " + cau_hinh.ljust(16) + "".join(f"{hang[c]:>11.3f}" for c in cot))

    for mon in ket_qua[CAU_HINH[0]]["tung_mon"]:
        print(f"\n=== {mon}")
        print("  " + "cấu hình".ljust(16) + "".join(c.rjust(11) for c in cot))
        for cau_hinh in CAU_HINH:
            hang = ket_qua[cau_hinh]["tung_mon"][mon]
            print("  " + cau_hinh.ljust(16) + "".join(f"{hang[c]:>11.3f}" for c in cot))

    for nhom, cac_phep in ket_qua["kiem_dinh"].items():
        print(f"\n=== McNemar, {nhom}")
        for ten, kq in cac_phep.items():
            print(f"  {ten:<40} {kq['chi_sau_dung']:>4} {kq['chi_truoc_dung']:>4}   p = {kq['p']:.3f}")


def main() -> None:
    # Console Windows mặc định là cp1252, không in được tiếng Việt và sẽ ném
    # UnicodeEncodeError sau khi đã tốn hạn mức nhúng.
    # line_buffering: chạy nền và ghi ra tệp thì Python đệm theo khối, nên dòng tiến
    # độ chỉ hiện ra khi đã chạy xong, đúng lúc không còn cần nữa.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    bo_phan_tich = argparse.ArgumentParser(description=__doc__)
    bo_phan_tich.add_argument("pha", choices=["nap", "sinh-truy-van", "danh-gia"])
    bo_phan_tich.add_argument(
        "--mon", action="append", choices=list(MON),
        help="chỉ chạy môn này, lặp lại được. Mặc định là mọi môn.",
    )
    doi_so = bo_phan_tich.parse_args()
    cac_mon = doi_so.mon or list(MON)

    with _phien() as db:
        if doi_so.pha == "nap":
            nap(db, cac_mon)
        elif doi_so.pha == "sinh-truy-van":
            sinh_truy_van(db, cac_mon)
        else:
            danh_gia(db, cac_mon)


if __name__ == "__main__":
    main()
