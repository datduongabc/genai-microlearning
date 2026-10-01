import re
from dataclasses import dataclass
from uuid import UUID

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

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
    """Limits retrieval to one contiguous run of chunks of one document, e.g.
    the chapter a teacher picked from a textbook. Frozen so it can key a cache."""

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
    query_embedding = embed_text(query)
    return (
        _scoped(db.query(Chunk).filter(Chunk.course_id == course_id, Chunk.embedding.isnot(None)), scope)
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
        .all()
    )


# Cache BM25 index theo course_id -- 1 lần generate_module() gọi sparse_search
# nhiều lần (LO/Content, Quiz, Practice...) cho CÙNG course, trước đây build
# lại từ đầu mỗi lần dù corpus không đổi. Invalidate tường minh khi có chunk
# mới (invalidate_sparse_cache(), gọi từ documents.py sau khi upload) thay vì
# TTL đoán mò -- tránh phục vụ index cũ sau khi tài liệu mới được thêm.
# Khóa gồm cả phạm vi: một chương có corpus (và thống kê IDF) riêng, không
# được dùng chung index với cả course.
_bm25_cache: dict[tuple[UUID, ChunkScope | None], tuple[BM25Okapi, list[Chunk]]] = {}


def invalidate_sparse_cache(course_id: UUID) -> None:
    for key in [k for k in _bm25_cache if k[0] == course_id]:
        del _bm25_cache[key]


def sparse_search(
    db: Session, course_id: UUID, query: str, top_k: int = 20, scope: ChunkScope | None = None
) -> list[Chunk]:
    """BM25 tren toan bo chunk cua 1 course, hoac chi tren khoang chunk cua `scope`."""
    key = (course_id, scope)
    cached = _bm25_cache.get(key)
    if cached is None:
        chunks = _scoped(db.query(Chunk).filter(Chunk.course_id == course_id), scope).all()
        if not chunks:
            return []
        corpus = [_tokenize(c.content) for c in chunks]
        bm25 = BM25Okapi(corpus)
        _bm25_cache[key] = (bm25, chunks)
    else:
        bm25, chunks = cached

    scores = bm25.get_scores(_tokenize(query))
    ranked = sorted(zip(chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, score in ranked[:top_k] if score > 0]


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
    `retrieve()` ben duoi thay vi ham nay truc tiep."""
    dense = dense_search(db, course_id, query, top_k=candidate_k, scope=scope)
    sparse = sparse_search(db, course_id, query, top_k=candidate_k, scope=scope)
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
    """mục 2 kien truc: pipeline retrieval DUY NHAT cua toan he thong, dung
    chung cho moi thanh phan sinh noi dung (LO, Content, Slides, Example,
    Practice, Quiz, Explanation). chunk -> hybrid search (RRF) -> rerank ->
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
    scored = rerank_scored(query, candidates)
    if top_k is not None:
        return [c for c, _ in scored[:top_k]]
    k = _adaptive_k_largest_gap(scored, min_k=min_k)
    return [c for c, _ in scored[:k]]


def expand_with_parent_context(
    db: Session, chunks: list[Chunk], window: int = 2, scope: ChunkScope | None = None
) -> list[Chunk]:
    """Parent-Child Retrieval (mục 3.2 kiến trúc, GD2 must-have): mở rộng
    từng chunk đã truy xuất được (child, chính xác để tìm kiếm) thành 1
    "parent window" gồm `window` chunk liền kề trước/sau trong CÙNG tài liệu
    (theo chunk_index, không phải created_at -- xem comment ở Chunk model),
    gộp lại thành 1 khối văn bản liên tục. Dùng khi sinh Content/Slides để
    không bị mất mạch ngữ cảnh khi câu/công thức liên quan bị cắt sang chunk
    khác cạnh nó (vấn đề "orphan equations/references").

    Không sửa đổi chunk gốc trong bộ nhớ/DB -- trả về danh sách Chunk MỚI
    (transient, không add vào session) mà content đã là văn bản parent gộp,
    dùng trực tiếp được với format_source_chunks() như chunk thường.

    Lưu ý phạm vi: cửa sổ nhỏ (mặc định ±2 chunk) chỉ nối liền ngữ cảnh NGAY
    CẠNH chunk đã khớp -- không giúp tìm lại nội dung liên quan nhưng nằm ở
    vị trí XA trong cùng tài liệu (đó là việc của top_k rộng hơn ở tầng gọi,
    không phải của hàm này)."""
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

    expanded: list[Chunk] = []
    seen_ranges: set[tuple] = set()
    for child in chunks:
        lo_idx = max(0, child.chunk_index - window)
        hi_idx = child.chunk_index + window
        if scope is not None:
            # The window must not reach into the neighbouring section, or the
            # first and last chunk of a section would pull its text in.
            lo_idx = max(lo_idx, scope.first_chunk_index)
            hi_idx = min(hi_idx, scope.last_chunk_index)
        key = (child.document_id, lo_idx, hi_idx)
        if key in seen_ranges:
            continue  # 2 child gần nhau có thể cho ra cùng 1 parent window -- tránh lặp
        seen_ranges.add(key)

        siblings = [
            s for s in chunks_by_doc.get(child.document_id, [])
            if lo_idx <= s.chunk_index <= hi_idx
        ]
        merged_text = "\n\n".join(s.content for s in siblings)
        expanded.append(Chunk(
            id=child.id, document_id=child.document_id, course_id=child.course_id,
            chunk_index=child.chunk_index, content=merged_text, source_ref=child.source_ref,
        ))
    return expanded
