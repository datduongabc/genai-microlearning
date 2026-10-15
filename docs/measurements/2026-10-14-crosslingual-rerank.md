# Course chat cross-language retrieval — 2026-10-14

## Diagnosis and change

Fresh private-stack reproduction used the real HTTP upload/ingestion/chat routes, pgvector,
Qwen3 embeddings, BM25, RRF, reranker and gateway. No UI or generation pipeline changes.

Question: **Sự khác nhau giữa vun đống lên và vun đống xuống là gì?**
All 26 heap.pdf chunks entered reranking. BM25 returned zero literal matches. Dense ranked
ReheapDown first (cosine 0.27088107), ReheapUp eighth (0.23067008). BGE reordered ReheapUp
first (sigmoid 0.000037431993), ReheapDown eighth (0.00003742082). The chat budget of five
therefore excluded the ReheapDown definition. The answer returned “Tài liệu môn học không
đề cập nội dung này” without citations. The English control (“What is the difference between
reheap up and reheap down?”) answered correctly and cited both definitions. This is loss at
reranking/context selection, not missing source chunks or an answer-instruction problem.

Replaced BGE-base with **Jina AI jina-reranker-v2-base-multilingual, official ONNX INT8**;
rerank the original question, no translation call. Increased course chat top_k from five to
eight. Both chat and generation retain the shared `retrieval/reranker.py` HTTP `/rerank`
client; generation's Adaptive-k floors remain four/eight. Embedding model, corpus/chunk
budget, BM25/RRF and answer instructions are unchanged.

## Runtime, provenance and license

- Runtime: `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4`, CPU, four-CPU quota.
- Jina revision: `9cfeff2df7d40d1b78e75e5e9cebec92a99813c9`.
- Official graph: `onnx/model_quantized.onnx`, 279,577,152 bytes, SHA-256
  `c5220cf8fe023f8aa0ed2a3eb787d4451a7f17cf53f6b787e35718dd4b8815c3`.
- Build prepares the graph as `/model/onnx/model.onnx` for TEI and adds
  `model_type=xlm-roberta` to local metadata; weights and tokenizer remain original.
  Checksum mismatch aborts the build. Runtime does not fetch weights or execute Hub code.
- Attribution: Jina AI. **CC-BY-NC-4.0, non-commercial research/thesis use**.
  The model card is kept in the image; commercial deployment needs suitable licensing.
  [Pinned Jina model card](https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual/blob/9cfeff2df7d40d1b78e75e5e9cebec92a99813c9/README.md),
  [official INT8 graph](https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual/tree/9cfeff2df7d40d1b78e75e5e9cebec92a99813c9/onnx).
- Baseline: BAAI/bge-reranker-base revision `2cfc18c9415c912f9d8155881c133215df768a70`.
- Qwen3 embedding remains ONNX revision `c25a394dd583836952667c12f008335071b3f43d`,
  last-token pooling, 768 dimensions, normalized as in ingestion.

## Measurement method

Re-ran the eight research pilot questions (Vietnamese and English) on the same two PDFs per
subject and source labels, with real pgvector/BM25/RRF, separately from the larger benchmark.
Gold means all required chunks, including both definitions for the heap comparisons.
Added a Vietnamese source/control. Pilot labels are a small previously observed development
set, not held-out evidence or statistically significant proof of general superiority.

Used the existing `backend/scripts/benchmark_rerank.py` method for four subjects. DSA uses
first five PDFs, operating-system first seven, program-fundamentals and web-programming
first seven, sorted by filename. Documents read/chunked in the backend image with its OCR
fallback. Fifty source chunks per subject, >=300 characters, sampled with seed 20260923;
questions generated using the existing no-examples prompt and groq-pro alias in the source
language. Identical questions, chunks and embeddings are scored before and after. These are
new source-derived queries; historical benchmark UUIDs cannot be relabeled without their
original database. Source-chunk recovery is a proxy, not a fully judged relevance set.

Dense/sparse retrieve 30, positive BM25 only; RRF k=60 keeps 30. Each model returns all
candidate scores with sigmoid and truncate=true. Cutoffs and Adaptive-k use the product
helper (largest gap in first 90%, buffer five). Report min_k four/eight separately. Model
scores cannot be compared as calibrated probabilities across models. Timings include one
HTTP rerank request; CPU quotas are equal but the shared host and sequential runs preclude
a controlled speedup claim. Memory is Docker cgroup current/peak, including runtime/cache,
not process RSS. No concurrency load benchmark.

## Paired benchmark results (200 source-derived questions)

| Subject | n | BGE → Jina recall@1 | recall@8 | recall@10 | Adaptive floor 4 | Adaptive floor 8 |
|---|---:|---|---|---|---|---|
| all | 200 | 0.725 → 0.855 | 0.945 → 0.985 | 0.970 → 0.990 | 0.940 → 0.990 | 0.945 → 0.990 |
| dsa | 50 | 0.600 → 0.740 | 0.960 → 0.980 | 0.960 → 0.980 | 0.960 → 0.980 | 0.960 → 0.980 |
| operating-system | 50 | 0.780 → 0.940 | 0.960 → 1.000 | 0.980 → 1.000 | 0.960 → 1.000 | 0.960 → 1.000 |
| program-fundamentals | 50 | 0.700 → 0.820 | 0.900 → 0.980 | 0.960 → 0.980 | 0.900 → 0.980 | 0.900 → 0.980 |
| web-programming | 50 | 0.820 → 0.920 | 0.960 → 0.980 | 0.980 → 1.000 | 0.940 → 1.000 | 0.960 → 1.000 |

All 200 candidate sets matched between models. Recall@8 improves in aggregate in every subject. Nine queries recover their labeled source only with Jina at eight, one only with BGE (paired exact McNemar p=0.021484375 overall; no multiple-comparison correction). The individual loss is “CSS được viết tắt từ cụm từ nào?”, CSS.pdf #1, rank 4 → 9. A separate live model probe still answers it at eight from a valid CSS citation. Source-derived question language is whatever the unchanged generation prompt produces; do not treat this mixed-subject set as a pure English-language benchmark. This is source-chunk recovery on development data, not a guarantee of non-regression on every question.

| Reranker | Median HTTP rerank | p95 HTTP rerank | Peak cgroup memory |
|---|---:|---:|---:|
| base | 5.61 s | 8.20 s | 2.57 GiB |
| jina | 2.78 s | 4.35 s | 1.73 GiB |

Qwen embedder peak: 3.20 GiB. Peaks were measured independently; their sum is not whole-stack concurrent memory. Some BGE scoring overlapped question generation to reuse saved results. This shared-host run is not a controlled speedup experiment. p95 uses the sorted sample at floor(0.95 × (n−1)).

## Research pilot and live checks

| Check | BGE | Jina INT8 |
|---|---|---|
| Vietnamese pilot, all required sources @8 | 3/8 | 7/8 |
| English pilot, all required sources @8 | 7/8 | 8/8 |
| Vietnamese source control, gold rank | 1 | 1 |
| Fresh uploaded heap, original Vietnamese reheap comparison | Refusal, no citation | Correct Vietnamese answer, both definitions cited |
| Same uploaded heap, English reheap comparison | Correct, both cited | Correct, both cited |
| Uploaded Vietnamese binary-search note | Pilot source control above | Correct Vietnamese answer, citation persisted in history |
| Heap max/min question on fresh uploaded heap | — | **Still refuses** (gold ranks 14/16; known limitation accepted by the project owner on 2026-10-15) |

Original reheap question after Jina (5.14 s): “ReheapUp … phần tử cuối cùng lên trên cây … ReheapDown … đẩy gốc xuống dưới cây”. Citations point to heap.pdf pages 10–11 and 12–13; source UUIDs are the same uploaded chunks used by the baseline. English control takes 6.15 s; Vietnamese binary-search control takes 1.40 s. Persisted history contains the original question and the same citation list as the returned assistant message.

### Known limitation: Vietnamese max/min comparison

“Đống cực đại và đống cực tiểu khác nhau ở quan hệ khóa giữa nút cha và nút con như thế nào?” still returns “Tài liệu môn học không đề cập nội dung này”. In the mixed-document pilot, max/min gold chunks #2/#3 rank 9/4, so all-gold@8 is 7/8 rather than the exploratory research result of 8/8. In the real uploaded heap-only course they rank **14/16**, scores 0.051005293/0.0496394. The exact same texts score 0.05297768/0.058730803 in the pilot request. The query is byte-identical. Different candidate pools/batches produce different scores; the cause is not established. Do not infer model/runtime equivalence from the research result alone. A controlled answer probe at budgets eight and nine still refuses. Production remains at the authorized budget eight; no answer instructions, embedding model or generation floors were changed to hide this failure. The original O1 reheap comparison is fixed; cross-language recall remains imperfect. The project owner authorized shipping Jina and the eight-chunk chat budget with this limitation recorded. On 2026-10-15 the project owner explicitly accepted this known limitation (Vietnamese max/min comparison still refuses, gold ranks 14/16) and approved shipping the Jina reranker as-is. No further max/min tuning is included in this change.

### Fresh original-source live checks — 2026-10-15

Repeated the previously untested provider-backed scenarios on a fresh private stack using
real HTTP registration/login, course creation, upload, background ingestion, chat, and history.
The original English **heap.pdf slide deck** (not a synthetic replacement) has SHA-256
`c7e30f1026a43309ffe6c486e14873a3e2b7b60864f67309bc3140cf37b742b4` and produced 26 chunks.
The stack used the committed Qwen3/Jina pipeline and an actual `gemini-fast` request;
each answer records served model `gemini-3.5-flash-lite`. Provider credentials were loaded
directly at runtime from the authorized environment file and are absent from the evidence.
There was no stand-in for the answer call. All task containers and the task network were removed.

| Live question | Actual result | Citations | Required-source ranks |
|---|---|---:|---|
| Sự khác nhau giữa vun đống lên và vun đống xuống là gì? | Correct Vietnamese comparison: move the final element up versus move the root down to restore the heap | 3 | ReheapUp #5 rank 1 (0.07460802), ReheapDown #6 rank 6 (0.06550622) |
| Thuật toán vun đống lên (ReheapUp) khôi phục tính chất đống bằng cách nào? | Correct Vietnamese explanation of moving the element upward to its proper position | 2 | Source passages and scores in artifact |
| Hàng đợi ưu tiên dùng đống để phục vụ phần tử theo thứ tự nào? | Correct Vietnamese answer: priority order, arrival order for ties | 1 | Source passages and scores in artifact |
| Đống cực đại và đống cực tiểu khác nhau ở quan hệ khóa giữa nút cha và nút con như thế nào? | **Tested: refusal without citations**, reproducing the accepted limitation | 0 | Max #2 rank 7 (0.057059567), min #3 rank 13 (0.05024043) |
| What is the difference between reheap up and reheap down? | Correct English comparison | 4 | ReheapUp #5 rank 5, ReheapDown #6 rank 2 |
| Tìm kiếm nhị phân hoạt động như thế nào? (separate Vietnamese source control) | Correct Vietnamese answer from the uploaded Vietnamese note | 1 | Source rank 1 |

Returned citations matched the persisted assistant history for all six requests. The reheap
comparison cites the actual uploaded English definitions (pages 10–13), and its explanation
matches their directions of movement. The max/min gold passages explicitly define max-heap
keys as greater than or equal to descendants and min-heap keys as less than or equal to
descendants; only the max definition reached the eight-chunk context. Fresh ranks **7/13**
differ from the earlier **14/16** measurement, while the refusal persists. These are separate
observations; the older scores above are retained as historical evidence.

[2026-10-15-crosslingual-live.json](2026-10-15-crosslingual-live.json) records the exact upload
provenance, six real answers, citations/history, served models, and full dense/BM25/RRF/rerank
chunks and scores. This closes the two earlier untested live-evidence rows; it does not extend
the accepted scope to solve the max/min limitation.

## Automated checks and artifacts

| Check | Result |
|---|---|
| New HTTP/retrieval/citation budget regression, VI→EN / VI→VI / EN→EN | Red with five, green with eight |
| Targeted chat/reranker/retrieval suite | 18 passed |
| Full backend `pytest -q`, isolated DB | 1172 passed, 1 live-LLM deselected, 3 warnings |
| Ruff, whitespace check | Pass |
| Docker build and TEI health | Pinned INT8 checksum verified; healthy `/rerank` |
| Compose `config --no-interpolate --quiet`, explicit private project | Pass |

The deterministic regression guards budget and citation persistence; actual model interpretation is checked by the development pilot/live runs above. The full-suite warnings are the existing AnyIO deprecation, short-key JWT test warning, and deletion SAWarning. The initial no-mistakes run validated and published this branch; a documentation follow-up records the fresh live checks above.

- [2026-10-14-crosslingual-benchmark.json](2026-10-14-crosslingual-benchmark.json): all 200 question/source labels, before/after candidate scores, source refs, metrics and memory.
- [2026-10-14-crosslingual-pilot.json](2026-10-14-crosslingual-pilot.json): exact pilot questions, both languages, required sources and scored retrieval.
- [2026-10-14-crosslingual-live.json](2026-10-14-crosslingual-live.json): real API answers, citations/history, dense/BM25/RRF/rerank traces and failed budget probes.
