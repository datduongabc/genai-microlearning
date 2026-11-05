import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

from rank_bm25 import BM25Okapi
from sqlalchemy import String, cast, func, literal_column
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from app import timing
from app.ingestion.embeddings import embed_text
from app.models import Chunk
from app.retrieval.reranker import rerank_scored

# mục 2 kien truc: RRF (k=60, tu code) tron Dense + Sparse ma khong can
# tinh chinh trong so thu cong.
RRF_K = 60

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class ChunkScope:
    """Limits retrieval to a document and an optional contiguous chunk range.
    Frozen so it can key a cache."""

    document_id: UUID
    first_chunk_index: int = 0
    last_chunk_index: int = 2**31 - 1  # Full range of the stored Integer chunk index.


def _scoped(query, scope: ChunkScope | None):
    if scope is None:
        return query
    return query.filter(
        Chunk.document_id == scope.document_id,
        Chunk.chunk_index.between(scope.first_chunk_index, scope.last_chunk_index),
    )


def dense_search(
    db: Session, course_id: UUID, query: str, top_k: int = 20, scope: ChunkScope | None = None
) -> list[Chunk]:
    """Cosine similarity qua pgvector. Filter course_id o WHERE clause -- ap
    dung TRUOC khi vao vector search (mục 4: hard metadata filtering,
    khong tin tuong LLM tu loc quyen). `scope` thu hep tiep xuong 1 khoang
    chunk cua 1 tai lieu, cung o WHERE clause."""
    with timing.timed("embed_query"):
        query_embedding = embed_text(query)
    with timing.timed("vector_search"):
        return (
            _scoped(db.query(Chunk).filter(Chunk.course_id == course_id, Chunk.embedding.isnot(None)), scope)
            .order_by(Chunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
            .all()
        )


# BM25 indexes per course and scope, because one generation searches the same course many times.
# Each entry carries a fingerprint of its chunk ids, compared on every search: chunks are written by
# the worker, usually another process, which cannot clear this process's cache. The cache only
# holds chunk ids, not ORM objects, because it outlives the session that loaded them.
_BM25_CACHE_MAX_ENTRIES = 64
_bm25_cache: OrderedDict[tuple[UUID, ChunkScope | None], tuple[str, BM25Okapi, list[UUID]]] = OrderedDict()
_bm25_cache_lock = threading.Lock()


def invalidate_sparse_cache(course_id: UUID) -> None:
    with _bm25_cache_lock:
        for key in [k for k in _bm25_cache if k[0] == course_id]:
            del _bm25_cache[key]


def _corpus_fingerprint(db: Session, course_id: UUID, scope: ChunkScope | None) -> str | None:
    """Changes whenever a chunk is added or removed; chunk text is never edited in place. None when
    this session sees no chunks, which is also what row level security gives a non-owner."""
    ids = func.string_agg(cast(Chunk.id, String), aggregate_order_by(literal_column("''"), Chunk.id))
    return _scoped(db.query(func.md5(ids)).filter(Chunk.course_id == course_id), scope).scalar()


def _bm25_index(db: Session, course_id: UUID, scope: ChunkScope | None) -> tuple[BM25Okapi, list[UUID]] | None:
    fingerprint = _corpus_fingerprint(db, course_id, scope)
    if fingerprint is None:
        return None
    key = (course_id, scope)
    with _bm25_cache_lock:
        cached = _bm25_cache.get(key)
        if cached is not None and cached[0] == fingerprint:
            _bm25_cache.move_to_end(key)
            return cached[1], cached[2]
    rows = _scoped(db.query(Chunk.id, Chunk.content).filter(Chunk.course_id == course_id), scope).all()
    if not rows:
        return None
    bm25 = BM25Okapi([_tokenize(content) for _, content in rows])
    chunk_ids = [chunk_id for chunk_id, _ in rows]
    with _bm25_cache_lock:
        _bm25_cache[key] = (fingerprint, bm25, chunk_ids)
        _bm25_cache.move_to_end(key)
        while len(_bm25_cache) > _BM25_CACHE_MAX_ENTRIES:
            _bm25_cache.popitem(last=False)
    return bm25, chunk_ids


def sparse_search(
    db: Session, course_id: UUID, query: str, top_k: int = 20, scope: ChunkScope | None = None
) -> list[Chunk]:
    """BM25 tren toan bo chunk cua 1 course, hoac chi tren khoang chunk cua `scope`."""
    with timing.timed("bm25"):
        index = _bm25_index(db, course_id, scope)
        if index is None:
            return []
        bm25, chunk_ids = index

        scores = bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(chunk_ids, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    top_ids = [chunk_id for chunk_id, score in ranked[:top_k] if score > 0]
    if not top_ids:
        return []
    by_id = {c.id: c for c in db.query(Chunk).filter(Chunk.id.in_(top_ids))}
    # A chunk deleted since the index was built is simply absent from by_id.
    return [by_id[chunk_id] for chunk_id in top_ids if chunk_id in by_id]


def reciprocal_rank_fusion(rankings: list[list[Chunk]], k: int = RRF_K) -> list[Chunk]:
    scores: dict[UUID, float] = {}
    chunk_by_id: dict[UUID, Chunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank + 1)
            chunk_by_id[chunk.id] = chunk
    ordered_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [chunk_by_id[cid] for cid in ordered_ids]


def hybrid_search(
    db: Session, course_id: UUID, query: str, top_k: int = 8, candidate_k: int = 20,
    scope: ChunkScope | None = None,
) -> list[Chunk]:
    """Dense + Sparse + RRF, khong rerank. Dung khi can so sanh truoc/sau
    rerank (xem scripts/try_retrieval.py); pipeline sinh noi dung nen goi
    `retrieve_for_generation()` ben duoi thay vi ham nay truc tiep."""
    dense = dense_search(db, course_id, query, top_k=candidate_k, scope=scope)
    sparse = sparse_search(db, course_id, query, top_k=candidate_k, scope=scope)
    with timing.timed("rrf"):
        fused = reciprocal_rank_fusion([dense, sparse])
    return fused[:top_k]


def _adaptive_k_largest_gap(
    scored: list[tuple[Chunk, float]], buffer: int = 5, search_fraction: float = 0.9, min_k: int = 2
) -> int:
    """Adaptive-k qua "khoảng cách lớn nhất" (Taguchi, Maekawa & Bhutani,
    "Efficient Context Selection for Long-Context QA: No Tuning, No
    Iteration, Just Adaptive-k", EMNLP 2025 -- Algorithm 1, arXiv:2506.08479):
    sắp điểm giảm dần, tính hiệu số liên tiếp (gap) trong 90% đầu (giới hạn
    tìm kiếm để tránh gap lớn nhất rơi vào đuôi ít liên quan -- Hình 1 của
    paper), cắt ngay TRƯỚC vị trí có gap lớn nhất, rồi lấy thêm `buffer` mục
    phía sau (mặc định 5, đúng giá trị paper dùng trong thực nghiệm) để
    không hụt mất chunk liên quan nằm sát ngay sau ngưỡng.

    Trả về SỐ LƯỢNG chunk nên giữ lại (không phải chỉ số)."""
    n = len(scored)
    if n <= min_k:
        return n
    search_limit = max(min_k, int(n * search_fraction))
    gaps = [scored[i][1] - scored[i + 1][1] for i in range(search_limit - 1)]
    if not gaps:
        return n
    largest_gap = max(gaps)
    if largest_gap <= 0:
        # Phân bố phẳng (không gap nào thực sự phân tách relevant/irrelevant)
        # -- argmax của mảng toàn 0 sẽ trả về index 0, gây cắt hụt ngay từ
        # chunk đầu tiên dù không có căn cứ gì để cắt sớm. Giữ nguyên cả cửa
        # sổ tìm kiếm thay vì cắt sai.
        return min(n, search_limit)
    cut_at = gaps.index(largest_gap) + 1  # index ngay sau gap lớn nhất = số chunk giữ lại
    return min(n, max(min_k, cut_at + buffer))


def retrieve(
    db: Session, course_id: UUID, query: str, top_k: int | None = 8, candidate_k: int = 30,
    min_k: int = 2, scope: ChunkScope | None = None,
) -> list[Chunk]:
    """mục 2 kien truc: pipeline retrieval chung cua toan he thong;
    sinh module dung `retrieve_for_generation()` de tach transaction doc.
    chunk -> hybrid search (RRF) -> rerank ->
    top-k chunk hyper-relevant (mục 3.1).

    top_k=int (mặc định, dùng cho Quiz/Practice -- 1 câu hỏi hẹp, chính xác
    hơn quan trọng hơn bao phủ rộng): cắt đúng top_k như cũ.
    top_k=None (dùng cho LO/Content -- cần thấy đủ rộng tài liệu để không
    hội tụ về 1 bloom_level "an toàn", xem GD2 Tuần 1): Adaptive-k, tự chọn
    số lượng theo phân bố điểm rerank thay vì số cố định.

    min_k: sàn tối thiểu cho Adaptive-k (mặc định 2, đúng giá trị paper thử
    nghiệm trên factoid QA). Cho tác vụ CẦN bao phủ rộng (LO/Content, bản
    chất là aggregation task theo đúng phân loại của paper -- "aggregation
    tasks... require comprehensive evidence and holistic understanding")
    PHẢI truyền min_k cao hơn ở tầng gọi (modules.py dùng 8) -- min_k=2 mặc
    định chỉ phù hợp câu hỏi factoid hẹp như Quiz, không phù hợp tổng hợp
    toàn khái niệm. Verify thật: với min_k=2, Adaptive-k từng chỉ chọn 6/13
    chunk của 1 tài liệu và bỏ sót pseudocode BFS/DFS dù có sẵn trong nguồn."""
    candidates = hybrid_search(db, course_id, query, top_k=candidate_k, candidate_k=candidate_k, scope=scope)
    return _reranked(query, candidates, top_k, min_k)


def retrieve_for_generation(
    db: Session, course_id: UUID, query: str, top_k: int | None = 8, candidate_k: int = 30,
    min_k: int = 2, scope: ChunkScope | None = None,
) -> list[Chunk]:
    # Generation has no pending writes here. Keep passages loaded after the read session closes,
    # so neither embedding (before the queries) nor reranking (after them) holds a transaction.
    with Session(bind=db.get_bind()) as reads:
        candidates = hybrid_search(reads, course_id, query, top_k=candidate_k,
                                   candidate_k=candidate_k, scope=scope)
    return _reranked(query, candidates, top_k, min_k)


def _reranked(query: str, candidates: list[Chunk], top_k: int | None, min_k: int) -> list[Chunk]:
    scored = rerank_scored(query, candidates)
    if top_k is not None:
        return [c for c, _ in scored[:top_k]]
    k = _adaptive_k_largest_gap(scored, min_k=min_k)
    return [c for c, _ in scored[:k]]


def expand_with_parent_context(
    db: Session, chunks: list[Chunk], window: int = 2, scope: ChunkScope | None = None
) -> list[Chunk]:
    """Add neighbouring physical chunks in document order, within the requested scope.

    Every returned chunk retains its own ID, text and locator: one evidence unit per
    source code, including neighbours added for parent context. Overlaps appear once.
    """
    if not chunks:
        return []

    # Trước đây: 1 truy vấn/child chunk (N+1, 8-15 round-trip/lần sinh
    # module). Gộp thành 1 truy vấn duy nhất -- lấy hết chunk của các
    # document liên quan rồi cắt window trong Python (số chunk/document
    # thường nhỏ, dư sức giữ trong bộ nhớ).
    doc_ids = {c.document_id for c in chunks}
    doc_chunks_query = db.query(Chunk).filter(Chunk.document_id.in_(doc_ids))
    if scope is not None:
        doc_chunks_query = doc_chunks_query.filter(
            Chunk.chunk_index.between(scope.first_chunk_index, scope.last_chunk_index)
        )
    all_doc_chunks = doc_chunks_query.order_by(Chunk.chunk_index).all()
    chunks_by_doc: dict = {}
    for c in all_doc_chunks:
        chunks_by_doc.setdefault(c.document_id, []).append(c)

    expanded: dict = {}
    for child in chunks:
        lo_idx = max(0, child.chunk_index - window)
        hi_idx = child.chunk_index + window
        if scope is not None:
            # Keep the parent window within the caller's requested chunk range.
            lo_idx = max(lo_idx, scope.first_chunk_index)
            hi_idx = min(hi_idx, scope.last_chunk_index)
        for sibling in chunks_by_doc.get(child.document_id, []):
            if lo_idx <= sibling.chunk_index <= hi_idx:
                expanded[sibling.id] = sibling
    # Preserve document relevance order, and physical reading order within each document.
    doc_order = {doc_id: i for i, doc_id in enumerate(dict.fromkeys(c.document_id for c in chunks))}
    return sorted(expanded.values(), key=lambda c: (doc_order[c.document_id], c.chunk_index))
