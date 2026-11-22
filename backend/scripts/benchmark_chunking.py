"""Retrieval before and after the PDF heading fix of PR #105, on one question set.

Before #105 every PDF line that began with a number counted as a section heading, so page counters,
code and list items cut chunks. The retrieval numbers of the report were measured on that cutting,
and its question set cannot be carried over: it kept only the random id of each source chunk, and
that database is gone. This script therefore measures both cuttings on new questions.

    python scripts/benchmark_chunking.py nap            # read once, cut both ways, embed
    python scripts/benchmark_chunking.py sinh-truy-van  # 50 questions per subject from new chunks
    python scripts/benchmark_chunking.py danh-gia       # dense, hybrid, hybrid + Jev on both

- Each PDF is read once per rule with the formula re-reads cached by page image, so the paid vision
  model sees a page once; only the heading rule differs between the two readings.
- Machine Learning is video and audio, which #105 does not touch; its 2026-11-08 transcripts are
  copied from the main database (MAIN_DATABASE_URL) instead of paying Whisper again.
- The questions come from new chunks, chosen in (subject, file, chunk index) order with a fixed seed,
  so a rerun picks the same chunks. For the old cutting the correct chunk is the old chunk of the
  same file that shares the most words with the new one, and the overlap is stored with it.
- Jev answers are cached by (question, passage), so chunks that are the same in both cuttings are
  scored once and a rerun spends nothing. A Jev fallback stops the run instead of silently scoring
  the search order.

Runs inside the backend image (tesseract). Environment: BENCHMARK_DATABASE_URL, MAIN_DATABASE_URL,
BENCHMARK_DOCUMENTS, BENCHMARK_KET_QUA, and the gateway and TypeSafe settings of the backend.
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import uuid
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_rerank import CAC_MUC_K, TOI_THIEU_KY_TU, _chi_so, _hoi_llm, _mcnemar, _trung

from app.config import settings
from app.ingestion import document_readers
from app.ingestion.chunker import chunk_units, document_title, locator_heading
from app.ingestion.document_readers import read_document
from app.ingestion.embeddings import embed_texts
from app.models.models import Chunk, Course, Document, DocumentStatus, User
from app.retrieval import reranker
from app.retrieval.search import _adaptive_k_largest_gap, dense_search, hybrid_search, invalidate_sparse_cache

GOC = Path(__file__).resolve().parents[2]
TAI_LIEU = Path(os.environ.get("BENCHMARK_DOCUMENTS") or GOC / "docs" / "documents")
KET_QUA = Path(os.environ.get("BENCHMARK_KET_QUA") or GOC / "docs" / "measurements" / "chunking")

MON = ("advanced-programming", "calculus 1", "machine-learning", "operating-system", "scientific-socialism")
MON_GHI_AM = "machine-learning"
CACH_CAT = ("cu", "moi")
CAU_HINH = ("dense", "hybrid", "hybrid+jev")
TRUY_VAN_MOI_MON = 50
HAT_GIONG = 20261112
UNG_VIEN = 30
SAN_ADAPTIVE = 8

# The rule PR #105 replaced, copied from document_readers.py at 5e04a9f.
TIEU_DE_CU = re.compile(r"^\s*(\d+(\.\d+)*)\.?\s+\S")
TIEU_DE_MOI = document_readers.is_numbered_heading


def _phien(bien: str = "BENCHMARK_DATABASE_URL") -> Session:
    url = os.environ.get(bien)
    if not url:
        sys.exit(f"thiếu {bien}")
    return Session(create_engine(url, future=True))


def _tep(ten: str) -> Path:
    KET_QUA.mkdir(parents=True, exist_ok=True)
    return KET_QUA / ten


class _BoNhoJsonl:
    """A dict persisted as one JSON line per entry, safe to fill from several threads."""

    def __init__(self, ten: str) -> None:
        self._duong = _tep(ten)
        self._khoa = threading.Lock()
        self.du_lieu = {}
        if self._duong.exists():
            for dong in self._duong.read_text(encoding="utf-8").splitlines():
                muc = json.loads(dong)
                self.du_lieu[muc["key"]] = muc["value"]

    def lay_hoac_tinh(self, khoa: str, tinh):
        with self._khoa:
            if khoa in self.du_lieu:
                return self.du_lieu[khoa]
        gia_tri = tinh()
        with self._khoa:
            self.du_lieu[khoa] = gia_tri
            with self._duong.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": khoa, "value": gia_tri}, ensure_ascii=False) + "\n")
        return gia_tri


def _bam(*phan: str | bytes) -> str:
    h = hashlib.sha256()
    for p in phan:
        h.update(p if isinstance(p, bytes) else p.encode())
        h.update(b"\0")
    return h.hexdigest()


# --- phase 1 -------------------------------------------------------------------------------


def _doc_hai_cach(tep: Path, khoa: str) -> dict[str, list]:
    """Units of one file under the old and the new heading rule; one vision call per page image."""
    if tep.suffix.lower() != ".pdf":
        units = read_document(str(tep), course_id=khoa, source_id=None)
        return {"cu": units, "moi": units}
    bo_nho = _BoNhoJsonl("vision_cache.jsonl")
    goc = document_readers.repair_page
    def doc_lai(png: bytes, lop_chu: str) -> str | None:
        # The reader would turn a failed call into a warning and keep the broken text, so one cutting
        # could get the repaired page and the other not. Stop instead; a rerun reuses the cache.
        def goi() -> str | None:
            try:
                return goc(png, lop_chu)
            except Exception as loi:
                # Raised inside the reader's thread pool, where an exception would be swallowed.
                print(f"vision failed, rerun nap: {loi}", file=sys.stderr, flush=True)
                os._exit(3)
        return bo_nho.lay_hoac_tinh(_bam(settings.formula_vision_model, png, lop_chu), goi)
    document_readers.repair_page = doc_lai
    try:
        ket_qua = {}
        for cach, quy_tac in (("moi", TIEU_DE_MOI), ("cu", lambda dong: bool(TIEU_DE_CU.match(dong)))):
            document_readers.is_numbered_heading = quy_tac
            ket_qua[cach] = read_document(str(tep), course_id=khoa, source_id=None)
        return ket_qua
    finally:
        document_readers.repair_page = goc
        document_readers.is_numbered_heading = TIEU_DE_MOI


def _ban_ghi_am(chinh: Session, ten_tep: str) -> list[tuple[str, str]]:
    """(content, source_ref) of the 2026-11-08 transcript of this file, in chunk order."""
    tai_lieu = chinh.execute(text(
        "select id from documents where filename = :f and status = 'embedded' and created_at::date = '2026-11-08' "
        "order by created_at limit 1"), {"f": ten_tep}).scalar_one()
    return [tuple(r) for r in chinh.execute(text(
        "select content, source_ref from chunks where document_id = :d order by chunk_index"), {"d": tai_lieu})]


def nap(db: Session) -> None:
    chu = db.query(User).first() or User(
        id=uuid.uuid4(), email="benchmark@example.com", name="Benchmark", hashed_password="khong-dang-nhap", is_admin=False)
    db.add(chu)
    db.flush()
    chinh = _phien("MAIN_DATABASE_URL")
    thong_ke = []
    for mon in MON:
        khoa_hoc = {}
        for cach in CACH_CAT:
            ten = f"{mon} | {cach}"
            if db.query(Course).filter(Course.name == ten).count():
                sys.exit(f"{ten} đã có; xoá database benchmark để nạp lại")
            khoa_hoc[cach] = Course(id=uuid.uuid4(), name=ten, owner_id=chu.id)
            db.add(khoa_hoc[cach])
        db.flush()
        for tep in sorted(p for p in (TAI_LIEU / mon).iterdir() if p.is_file()):
            if mon == MON_GHI_AM:
                cung = _ban_ghi_am(chinh, tep.name)
                hai_cach = {cach: cung for cach in CACH_CAT}
            else:
                units = _doc_hai_cach(tep, str(khoa_hoc["moi"].id))
                hai_cach = {
                    cach: [(c.content, json.dumps(c.locator, ensure_ascii=False))
                           for c in chunk_units(units[cach], title=document_title(tep.name))]
                    for cach in CACH_CAT
                }
            # One embedding per distinct text: most chunks are the same in both cuttings.
            khac_nhau = sorted({noi_dung for ds in hai_cach.values() for noi_dung, _ in ds})
            vector = dict(zip(khac_nhau, embed_texts(khac_nhau), strict=True))
            for cach in CACH_CAT:
                tai_lieu = Document(id=uuid.uuid4(), course_id=khoa_hoc[cach].id, filename=tep.name,
                                    status=DocumentStatus.embedded)
                db.add(tai_lieu)
                db.flush()
                for chi_so, (noi_dung, nguon) in enumerate(hai_cach[cach]):
                    db.add(Chunk(id=uuid.uuid4(), document_id=tai_lieu.id, course_id=khoa_hoc[cach].id,
                                 chunk_index=chi_so, content=noi_dung, source_ref=nguon, embedding=vector[noi_dung]))
            db.commit()
            nhan_cu = [locator_heading(json.loads(n)) for _, n in hai_cach["cu"]]
            nhan_moi = [locator_heading(json.loads(n)) for _, n in hai_cach["moi"]]
            thong_ke.append({
                "course": mon, "file": tep.name, "chunks_cu": len(hai_cach["cu"]), "chunks_moi": len(hai_cach["moi"]),
                "giong_nhau": len({c for c, _ in hai_cach["cu"]} & {c for c, _ in hai_cach["moi"]}),
                "nhan_cu": len(set(filter(None, nhan_cu))), "nhan_moi": len(set(filter(None, nhan_moi))),
            })
            _tep("nap.json").write_text(json.dumps(thong_ke, ensure_ascii=False, indent=2), encoding="utf-8")
            print(thong_ke[-1], flush=True)


# --- phase 2 -------------------------------------------------------------------------------


def _tu(van_ban: str) -> Counter:
    return Counter(re.findall(r"\w+", van_ban.lower()))


def _chunk_theo_thu_tu(db: Session, ten_khoa: str) -> list[tuple[str, Chunk]]:
    khoa = db.query(Course).filter(Course.name == ten_khoa).one()
    return [(f, c) for c, f in db.query(Chunk, Document.filename).join(Document, Chunk.document_id == Document.id)
            .filter(Chunk.course_id == khoa.id).order_by(Document.filename, Chunk.chunk_index).all()]


def sinh_truy_van(db: Session) -> None:
    rng = random.Random(HAT_GIONG)
    tep = _tep("truy_van.json")
    cu = {t["chunk_moi"]["content_sha"]: t for t in (json.loads(tep.read_text(encoding="utf-8")) if tep.exists() else [])}
    truy_van = []
    for mon in MON:
        moi = _chunk_theo_thu_tu(db, f"{mon} | moi")
        cu_theo_tep: dict[str, list[Chunk]] = {}
        for f, c in _chunk_theo_thu_tu(db, f"{mon} | cu"):
            cu_theo_tep.setdefault(f, []).append(c)
        ung_vien = [(f, c) for f, c in moi if len(c.content) >= TOI_THIEU_KY_TU]
        for f, c in rng.sample(ung_vien, min(TRUY_VAN_MOI_MON, len(ung_vien))):
            sha = _bam(c.content)
            if sha in cu:
                truy_van.append(cu[sha])
                continue
            tu_moi = _tu(c.content)
            diem = [(sum((tu_moi & _tu(o.content)).values()) / max(1, sum(tu_moi.values())), o) for o in cu_theo_tep[f]]
            trung, ban_cu = max(diem, key=lambda d: d[0])
            truy_van.append({
                "course": mon, "file": f, "cau_hoi": _hoi_llm(c.content),
                "chunk_moi": {"chunk_index": c.chunk_index, "content_sha": sha},
                "chunk_cu": {"chunk_index": ban_cu.chunk_index, "content_sha": _bam(ban_cu.content), "trung_tu": round(trung, 4)},
            })
            tep.write_text(json.dumps(truy_van, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{mon}: {len([t for t in truy_van if t['course'] == mon])} câu", flush=True)
    tep.write_text(json.dumps(truy_van, ensure_ascii=False, indent=2), encoding="utf-8")


# --- phase 3 -------------------------------------------------------------------------------


class JevDuPhong(RuntimeError):
    pass


def _jev(bo_nho: _BoNhoJsonl):
    goc = reranker._ask_jev

    def hoi(cau_hoi: str, van_ban: str, attempts: int = reranker.MAX_ATTEMPTS) -> tuple[float, int]:
        return bo_nho.lay_hoac_tinh(_bam(settings.jev_model, cau_hoi, van_ban), lambda: goc(cau_hoi, van_ban, attempts)[0]), 0
    return hoi


NGUONG_NOI = 0.5


def _dice(a: Counter, b: Counter) -> float:
    return 2 * sum((a & b).values()) / max(1, sum(a.values()) + sum(b.values()))


def _dung_cu(moi: Chunk, cu_cung_tep: list[Chunk]) -> dict:
    """The correct old chunks for a question written from `moi`: the identical chunk when there is
    one; otherwise, strictly, the one most similar by Dice, and leniently also every old chunk with at
    least half of its words inside `moi`, since a question on a span the old rule cut in two can be
    answered by either half."""
    giong = [c for c in cu_cung_tep if c.content == moi.content]
    if giong:
        sha = _bam(giong[0].content)
        return {"chat": {sha}, "noi": {sha}, "dice": 1.0, "giong": True}
    tu_moi = _tu(moi.content)
    diem = [(_dice(tu_moi, _tu(c.content)), c) for c in cu_cung_tep]
    dice, tot_nhat = max(diem, key=lambda d: d[0])
    noi = {_bam(c.content) for c in cu_cung_tep
           if sum((_tu(c.content) & tu_moi).values()) / max(1, sum(_tu(c.content).values())) >= NGUONG_NOI}
    return {"chat": {_bam(tot_nhat.content)}, "noi": noi | {_bam(tot_nhat.content)}, "dice": round(dice, 4), "giong": False}


def danh_gia(db: Session) -> None:
    bo_nho = _BoNhoJsonl("jev_cache.jsonl")
    reranker._ask_jev = _jev(bo_nho)
    truy_van = json.loads(_tep("truy_van.json").read_text(encoding="utf-8"))
    for mon in MON:
        moi = {(f, _bam(c.content)): c for f, c in _chunk_theo_thu_tu(db, f"{mon} | moi")}
        cu: dict[str, list[Chunk]] = {}
        for f, c in _chunk_theo_thu_tu(db, f"{mon} | cu"):
            cu.setdefault(f, []).append(c)
        for t in truy_van:
            if t["course"] == mon:
                t["dung"] = _dung_cu(moi[(t["file"], t["chunk_moi"]["content_sha"])], cu[t["file"]])
    print("câu có chunk cũ giống hệt:", sum(t["dung"]["giong"] for t in truy_van), "/", len(truy_van))
    chi_tiet = []
    for cach in CACH_CAT:
        khoa_hoc = {mon: db.query(Course).filter(Course.name == f"{mon} | {cach}").one() for mon in MON}
        for k in khoa_hoc.values():
            invalidate_sparse_cache(k.id)
        for chi_so, t in enumerate(truy_van, start=1):
            khoa = khoa_hoc[t["course"]].id
            cac_dung = {"moi": {"chat": {t["chunk_moi"]["content_sha"]}, "noi": {t["chunk_moi"]["content_sha"]}},
                        "cu": t["dung"]}[cach]
            dense = dense_search(db, khoa, t["cau_hoi"], top_k=10)
            ung_vien = hybrid_search(db, khoa, t["cau_hoi"], top_k=UNG_VIEN, candidate_k=UNG_VIEN)
            span = {}
            diem = reranker._jev_scores(t["cau_hoi"], ung_vien, span)
            if span.get("fallback"):
                raise JevDuPhong(f"Jev fell back on question {chi_so} ({cach}); nothing after it was scored")
            xep = [c for c, _ in sorted(zip(ung_vien, diem, strict=True), key=lambda p: p[1], reverse=True)]
            k_thich_ung = _adaptive_k_largest_gap(
                sorted(zip(ung_vien, diem, strict=True), key=lambda p: p[1], reverse=True), min_k=SAN_ADAPTIVE)

            for cham in ("chat", "noi"):
                dung = cac_dung[cham]

                def hang(ds: list[Chunk], dung=dung) -> int | None:
                    return next((i for i, c in enumerate(ds[:10], start=1) if _bam(c.content) in dung), None)
                chi_tiet.append({
                    "cach_cat": cach if cham == "chat" else f"{cach}-noi", "course": t["course"], "cau_hoi": t["cau_hoi"],
                    "dice_cu": t["dung"]["dice"],
                    "thu_hang": {"dense": hang(dense), "hybrid": hang(ung_vien), "hybrid+jev": hang(xep)},
                    "adaptive_k": {"k": k_thich_ung, "trung": any(_bam(c.content) in dung for c in xep[:k_thich_ung])},
                })
            if chi_so % 25 == 0:
                print(f"  {cach} {chi_so}/{len(truy_van)}", flush=True)
    _tep("thu_hang.json").write_text(json.dumps(chi_tiet, ensure_ascii=False, indent=2), encoding="utf-8")
    ket_qua = tong_hop(chi_tiet)
    _tep("ket_qua.json").write_text(json.dumps(ket_qua, ensure_ascii=False, indent=2), encoding="utf-8")
    _in(ket_qua)


def tong_hop(chi_tiet: list[dict]) -> dict:
    ket_qua: dict = {}
    for cach in ("cu", "moi", "cu-noi"):
        ds = [d for d in chi_tiet if d["cach_cat"] == cach]
        ket_qua[cach] = {
            ch: {"chung": _chi_so([d["thu_hang"][ch] for d in ds]),
                 "tung_mon": {m: _chi_so([d["thu_hang"][ch] for d in ds if d["course"] == m]) for m in MON}}
            for ch in CAU_HINH
        }
        ket_qua[cach]["adaptive_k"] = {"trung": sum(d["adaptive_k"]["trung"] for d in ds) / len(ds),
                                       "k_trung_binh": sum(d["adaptive_k"]["k"] for d in ds) / len(ds)}
        ket_qua[cach]["kiem_dinh"] = {
            f"{sau} so với {truoc}, recall@{k}": _mcnemar([_trung(d["thu_hang"][truoc], k) for d in ds],
                                                           [_trung(d["thu_hang"][sau], k) for d in ds])
            for truoc, sau in (("hybrid", "hybrid+jev"), ("dense", "hybrid"), ("dense", "hybrid+jev")) for k in (1, 10)
        }
    cu = [d for d in chi_tiet if d["cach_cat"] == "cu"]
    moi = [d for d in chi_tiet if d["cach_cat"] == "moi"]
    cu_noi = [d for d in chi_tiet if d["cach_cat"] == "cu-noi"]
    ket_qua["moi_so_voi_cu_noi"] = {
        f"{ch}, recall@{k}": _mcnemar([_trung(d["thu_hang"][ch], k) for d in cu_noi], [_trung(d["thu_hang"][ch], k) for d in moi])
        for ch in CAU_HINH for k in (1, 10)
    }
    ket_qua["moi_so_voi_cu"] = {
        f"{ch}, recall@{k}": _mcnemar([_trung(d["thu_hang"][ch], k) for d in cu], [_trung(d["thu_hang"][ch], k) for d in moi])
        for ch in CAU_HINH for k in (1, 10)
    }
    return ket_qua


def _in(ket_qua: dict) -> None:
    cot = [f"recall@{k}" for k in CAC_MUC_K] + ["mrr@10", "ndcg@10"]
    for cach in ("cu", "cu-noi", "moi"):
        print(f"\n=== cắt {cach}")
        for ch in CAU_HINH:
            hang = ket_qua[cach][ch]["chung"]
            print("  " + ch.ljust(12) + "".join(f"{hang[c]:>9.3f}" for c in cot))
        print("  adaptive-k", ket_qua[cach]["adaptive_k"])
    print("\n=== mới so với cũ (McNemar)")
    for ten, kq in [*ket_qua["moi_so_voi_cu"].items(), *(("nới " + k, v) for k, v in ket_qua["moi_so_voi_cu_noi"].items())]:
        print(f"  {ten:<26} {kq['chi_sau_dung']:>4} {kq['chi_truoc_dung']:>4}   p = {kq['p']:.3f}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pha", choices=["nap", "sinh-truy-van", "danh-gia"])
    pha = p.parse_args().pha
    with _phien() as db:
        {"nap": nap, "sinh-truy-van": sinh_truy_van, "danh-gia": danh_gia}[pha](db)


if __name__ == "__main__":
    main()
