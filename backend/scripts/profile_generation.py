"""Đo thời gian và tải của pipeline thật: nạp tài liệu, rồi sinh module, tuần tự và đồng thời.

Góp ý của thầy hướng dẫn: thời gian trung bình để sinh xong một module, tách theo ingestion, nhúng,
truy hồi (RRF + rerank) và thời gian sinh qua gateway; và mức tranh chấp tài nguyên khi nhiều lượt
chạy cùng lúc. Script gọi đúng các hàm mà worker gọi, không dựng một pipeline riêng để đo, nên số
liệu là của chính hệ thống.

    uv run python scripts/profile_generation.py nap
    uv run python scripts/profile_generation.py sinh --model claude-fast --dong-thoi 1
    uv run python scripts/profile_generation.py sinh --model claude-fast --dong-thoi 4 --so-module 8
    uv run python scripts/profile_generation.py bao-cao

`nap` đọc, cắt và nhúng mỗi tệp của năm môn, ghi thời gian từng bước. `sinh` sinh một module cho
mỗi tài liệu chưa có module của lượt đó (hoặc --so-module tài liệu), với --dong-thoi lượt chạy song
song như các thread của worker; mỗi module tự lưu thời gian từng giai đoạn (modules.generation_timings,
app/timing.py). `bao-cao` gộp tất cả thành trung bình, p50 và p95.

TTFT không áp dụng cho bước sinh: mọi lệnh gọi là structured output không stream, nên token đầu
tiên chỉ đến cùng toàn bộ câu trả lời; số đo là thời gian cả lệnh gọi.

Mức dùng CPU/RAM của các container lấy từ máy chủ bằng `docker stats` trong lúc chạy (script này
chạy trong container nên không thấy docker); thời điểm bắt đầu và kết thúc mỗi lượt được ghi lại để
đối chiếu.

PHẢI chạy trong container backend (tesseract, ffmpeg). Biến môi trường như benchmark_rerank.py:
BENCHMARK_DATABASE_URL, BENCHMARK_DOCUMENTS, BENCHMARK_KET_QUA.
"""

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.generation.module_generation_pipeline import run_module_generation
from app.ingestion.chunker import chunk_units, document_title
from app.ingestion.document_readers import read_document
from app.ingestion.embeddings import embed_texts
from app.llm import gateway
from app.models import Chunk, Course, Document, DocumentStatus, Module, User

GOC = Path(__file__).resolve().parents[2]
GOC_TAI_LIEU = Path(os.environ.get("BENCHMARK_DOCUMENTS") or GOC / "docs" / "documents")
THU_MUC_KET_QUA = Path(os.environ.get("BENCHMARK_KET_QUA") or GOC / "docs" / "measurements")
MON = ("advanced-programming", "calculus 1", "machine-learning", "operating-system", "scientific-socialism")
# Course của script này mang tiền tố riêng để không lẫn với course của benchmark truy hồi.
TIEN_TO = "profile:"
TEP_NAP = "profile_nap.json"
TEP_SINH = "profile_sinh.json"
TEP_BAO_CAO = "profile_bao_cao.json"


def _phien_lam() -> sessionmaker:
    url = os.environ.get("BENCHMARK_DATABASE_URL")
    if not url:
        sys.exit("thiếu BENCHMARK_DATABASE_URL")
    return sessionmaker(bind=create_engine(url, pool_size=16), autoflush=False)


def _tep(ten: str) -> Path:
    THU_MUC_KET_QUA.mkdir(parents=True, exist_ok=True)
    return THU_MUC_KET_QUA / ten


def _doc_json(ten: str) -> list:
    tep = _tep(ten)
    return json.loads(tep.read_text(encoding="utf-8")) if tep.exists() else []


def _ghi_json(ten: str, du_lieu) -> None:
    _tep(ten).write_text(json.dumps(du_lieu, ensure_ascii=False, indent=2), encoding="utf-8")


def _bay_gio() -> str:
    return datetime.now(UTC).isoformat()


# --- nạp ----------------------------------------------------------------------------------------


def nap(phien: sessionmaker) -> None:
    ket_qua = _doc_json(TEP_NAP)
    with phien() as db:
        chu = db.scalar(select(User).where(User.email == "profile@example.com"))
        if chu is None:
            chu = User(email="profile@example.com", name="Profile", hashed_password="khong-dung-de-dang-nhap")
            db.add(chu)
            db.commit()
        for mon in MON:
            if db.scalar(select(Course).where(Course.name == TIEN_TO + mon)) is not None:
                print(f"=== bỏ qua {mon}: đã nạp")
                continue
            khoa = Course(name=TIEN_TO + mon, owner_id=chu.id)
            db.add(khoa)
            db.commit()
            print(f"\n=== {mon}")
            for tep in sorted(p for p in (GOC_TAI_LIEU / mon).iterdir() if p.is_file()):
                t0 = time.perf_counter()
                units = read_document(str(tep), course_id=str(khoa.id), source_id=tep.name)
                t1 = time.perf_counter()
                cac_chunk = chunk_units(units, title=document_title(tep.name))
                t2 = time.perf_counter()
                vectors = embed_texts([c.content for c in cac_chunk])
                t3 = time.perf_counter()
                tai_lieu = Document(
                    course_id=khoa.id, filename=tep.name, status=DocumentStatus.embedded,
                    raw_text="\n\n".join(u.text for u in units if u.text),
                    extraction_warnings=[w for u in units for w in u.warnings],
                )
                db.add(tai_lieu)
                db.flush()
                db.add_all(
                    Chunk(document_id=tai_lieu.id, course_id=khoa.id, chunk_index=i, content=c.content,
                          source_ref=json.dumps(c.locator, ensure_ascii=False), embedding=v)
                    for i, (c, v) in enumerate(zip(cac_chunk, vectors, strict=True))
                )
                db.commit()
                ket_qua.append({
                    "course": mon, "file": tep.name, "bytes": tep.stat().st_size, "units": len(units),
                    "chunks": len(cac_chunk), "read_s": round(t1 - t0, 3), "chunk_s": round(t2 - t1, 3),
                    "embed_s": round(t3 - t2, 3), "embed_s_per_chunk": round((t3 - t2) / max(1, len(cac_chunk)), 4),
                })
                _ghi_json(TEP_NAP, ket_qua)
                print(f"  {tep.name[:48]:<48} {len(cac_chunk):>4} chunk  đọc {t1 - t0:6.1f}s  nhúng {t3 - t2:5.1f}s")


# --- sinh ---------------------------------------------------------------------------------------


def _sinh_mot(phien: sessionmaker, document_id: uuid.UUID, model: str, luot: str) -> dict:
    bat_dau = _bay_gio()
    t0 = time.perf_counter()
    try:
        # Mỗi thread một Session và ngữ cảnh model riêng, như các thread của worker.
        with phien() as db, gateway.experiment_mode(), gateway.using_model(model):
            module = run_module_generation(db, db.get(Document, document_id))
            return {"luot": luot, "document_id": str(document_id), "module_id": str(module.id),
                    "ok": True, "bat_dau": bat_dau, "ket_thuc": _bay_gio(), "giay": round(time.perf_counter() - t0, 2)}
    except Exception as loi:
        return {"luot": luot, "document_id": str(document_id), "ok": False, "loi": f"{type(loi).__name__}: {loi}"[:500],
                "bat_dau": bat_dau, "ket_thuc": _bay_gio(), "giay": round(time.perf_counter() - t0, 2)}


def sinh(phien: sessionmaker, model: str, dong_thoi: int, so_module: int | None) -> None:
    luot = f"{model}-x{dong_thoi}"
    ket_qua = _doc_json(TEP_SINH)
    xong = {r["document_id"] for r in ket_qua if r["luot"] == luot and r["ok"]}
    with phien() as db:
        cac_tai_lieu = db.scalars(
            select(Document.id).join(Course).where(Course.name.like(TIEN_TO + "%"))
            .order_by(Course.name, Document.filename)
        ).all()
    cho = [d for d in cac_tai_lieu if str(d) not in xong][:so_module]
    print(f"=== lượt {luot}: {len(cho)} module, {dong_thoi} lượt song song")
    t0, bat_dau = time.perf_counter(), _bay_gio()
    with ThreadPoolExecutor(max_workers=dong_thoi) as pool:
        for dong in pool.map(lambda d: _sinh_mot(phien, d, model, luot), cho):
            ket_qua.append(dong)
            _ghi_json(TEP_SINH, ket_qua)
            print(f"  {'ok ' if dong['ok'] else 'LỖI'} {dong['giay']:7.1f}s  {dong.get('loi', '')}")
    ket_qua.append({"luot": luot, "tong_ket": True, "dong_thoi": dong_thoi, "so_module": len(cho),
                    "bat_dau": bat_dau, "ket_thuc": _bay_gio(), "giay": round(time.perf_counter() - t0, 2)})
    _ghi_json(TEP_SINH, ket_qua)


# --- báo cáo ------------------------------------------------------------------------------------


def _thong_ke(gia_tri: list[float]) -> dict:
    if not gia_tri:
        return {"n": 0}
    xep = sorted(gia_tri)

    def phan_vi(p: float) -> float:
        return xep[min(len(xep) - 1, round(p * (len(xep) - 1)))]

    return {"n": len(xep), "mean": round(statistics.fmean(xep), 3), "p50": round(phan_vi(0.5), 3),
            "p95": round(phan_vi(0.95), 3), "max": round(xep[-1], 3)}


def bao_cao(phien: sessionmaker) -> None:
    sinh_ra = [r for r in _doc_json(TEP_SINH) if not r.get("tong_ket") and r["ok"]]
    luot_cua = {r["module_id"]: r["luot"] for r in sinh_ra}
    with phien() as db:
        thoi_gian = {str(m.id): m.generation_timings for m in db.scalars(
            select(Module).where(Module.id.in_([uuid.UUID(k) for k in luot_cua]))
        ) if m.generation_timings}

    bao = {"nap": {}, "sinh": {}, "dong_thoi": []}
    nap_ra = _doc_json(TEP_NAP)
    for mon in MON:
        cua_mon = [r for r in nap_ra if r["course"] == mon]
        if cua_mon:
            bao["nap"][mon] = {k: _thong_ke([r[k] for r in cua_mon])
                               for k in ("read_s", "chunk_s", "embed_s", "embed_s_per_chunk", "chunks")}

    for luot in sorted(set(luot_cua.values())):
        cua_luot = [t for mid, t in thoi_gian.items() if luot_cua[mid] == luot]
        giai_doan = sorted({s for t in cua_luot for s in t["summary"]})
        loai = sorted({s["kind"] for t in cua_luot for s in t["spans"] if s["kind"] != "stage"})
        bao["sinh"][luot] = {
            "tong": _thong_ke([t["total_seconds"] for t in cua_luot]),
            "giai_doan": {g: {
                "giay": _thong_ke([t["summary"][g]["seconds"] for t in cua_luot if g in t["summary"]]),
                "llm_calls": _thong_ke([t["summary"][g]["llm_calls"] for t in cua_luot if g in t["summary"]]),
            } for g in giai_doan},
            # Từng lệnh gọi riêng lẻ, gộp mọi giai đoạn: một lệnh gọi model, một lần nhúng câu truy vấn,...
            "moi_lenh_goi": {k: _thong_ke([s["seconds"] for t in cua_luot for s in t["spans"] if s["kind"] == k])
                             for k in loai},
            "token_moi_module": {k: _thong_ke([sum(s.get(k) or 0 for s in t["spans"] if s["kind"] == "llm")
                                               for t in cua_luot]) for k in ("prompt_tokens", "completion_tokens")},
        }
    for tong in (r for r in _doc_json(TEP_SINH) if r.get("tong_ket")):
        if tong["so_module"]:
            bao["dong_thoi"].append({**tong, "module_moi_gio": round(3600 * tong["so_module"] / tong["giay"], 2)})

    _ghi_json(TEP_BAO_CAO, bao)
    print(json.dumps(bao, ensure_ascii=False, indent=2))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pha", choices=["nap", "sinh", "bao-cao"])
    parser.add_argument("--model", default="claude-fast", help="alias đơn của gateway, được ghim (experiment mode)")
    parser.add_argument("--dong-thoi", type=int, default=1, help="số lượt sinh chạy song song")
    parser.add_argument("--so-module", type=int, help="chỉ sinh cho chừng này tài liệu")
    args = parser.parse_args()
    phien = _phien_lam()
    if args.pha == "nap":
        nap(phien)
    elif args.pha == "sinh":
        sinh(phien, args.model, args.dong_thoi, args.so_module)
    else:
        bao_cao(phien)


if __name__ == "__main__":
    main()
