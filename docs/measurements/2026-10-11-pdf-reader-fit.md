# PDF reader fit — refreshed 2026-10-26

The current corpus contains 11 PDFs / 681 pages: six calculus 1 PDFs (411 pages)
and five operating-system PDFs (270 pages). Advanced programming and scientific
socialism are PPTX; machine learning is audio/video. Only PDFs are measured here.
The owner made this corpus the reference source on 2026-10-26. Calculus 1 is
therefore in scope again, and the formula rules stay: limit/big-operator attachment,
large brackets, radicals, absolute-value bars and math-region rebuilding. Formula and
figure extraction improvements beyond the original rules are a separate follow-up.

Owner decisions applied: retain the VNI decoder; retain only this summary and the
measurement scripts in git; warn on a document only when both its text layer and
OCR leave a page completely empty; use Tesseract `vie+eng` at 200 dpi.
Raw per-file JSON, page text diffs, model responses and logs stay outside git in a
private evidence directory (`$EVIDENCE_DIR` below).

## Extraction measurement

Baseline is `760483b9825d646d785e78ef3c56989998142088`, the original rule set.
P/L/R below means plain PyMuPDF / original rules / revised rules. PyMuPDF version
is 1.28.2; timing is the mean of three text-layout passes per page on this host,
excluding OCR, rendering, chunking and model calls. It varies with host load.
A fragment is a nonempty line with at most three non-whitespace characters; genuine
short labels and formula terms count too. Changed characters count replaced,
inserted or deleted characters after removing whitespace, using the larger edit
length, not Levenshtein distance. These are structural diagnostics, not annotated
text accuracy or formula correctness. Revised calculus text is identical to the
original rules on all 411 pages; the revised OS changes are the code, bullet, table
and reading-order repairs described below. Leading spaces are kept only on lines with
a monospaced (code) span; ordinary prose and table-cell lines are left-trimmed.
Against the previous revision, that trimming changed leading whitespace only, on 18
OS pages (Processes 1, CPU Scheduling 8, Synchronization 9). Chunk counts, locators
and whitespace-insensitive chunk text are unchanged; calculus output is identical.

| Subject | Pages | Lines P/L/R | Fragments P/L/R | Fragment rate P/L/R | ms/page P/L/R |
| --- | ---: | --- | --- | --- | --- |
| calculus 1 | 411 | 9326 / 5564 / 5564 | 2975 / 585 / 585 | 31.90% / 10.51% / 10.51% | 2.935 / 8.094 / 8.273 |
| operating-system | 270 | 3440 / 3381 / 3366 | 587 / 501 / 486 | 17.06% / 14.82% / 14.44% | 0.872 / 2.214 / 1.546 |

| PDF | Pages | Fragment rate P/L/R | Changed chars P→L / P→R / L→R | ms/page P/L/R | Chunks L/R |
| --- | ---: | --- | --- | --- | --- |
| calculus 1/1. Giới hạn dãy số.pdf | 77 | 31.30% / 6.07% / 6.07% | 2933 / 2933 / 0 | 2.811 / 7.523 / 7.688 | 52 / 52 |
| calculus 1/2. Giới hạn và liên tục hàm một biến.pdf | 54 | 23.66% / 6.38% / 6.38% | 1334 / 1334 / 0 | 3.248 / 8.784 / 8.950 | 37 / 37 |
| calculus 1/3. Đạo hàm và vi phân.pdf | 107 | 23.93% / 6.84% / 6.84% | 3319 / 3319 / 0 | 3.103 / 8.495 / 8.645 | 74 / 74 |
| calculus 1/4. Tích phân xác định.pdf | 36 | 32.71% / 18.33% / 18.33% | 602 / 602 / 0 | 1.834 / 5.369 / 5.126 | 20 / 20 |
| calculus 1/5. Tích phân bất định.pdf | 68 | 38.84% / 9.03% / 9.03% | 2733 / 2733 / 0 | 2.205 / 7.183 / 7.416 | 48 / 48 |
| calculus 1/6. Tích phân suy rộng.pdf | 69 | 39.48% / 20.54% / 20.54% | 2145 / 2145 / 0 | 3.858 / 9.891 / 10.307 | 45 / 45 |
| operating-system/Chapter 2_Operating System Structures.pdf | 38 | 9.64% / 9.64% / 9.64% | 159 / 159 / 0 | 0.766 / 1.926 / 1.298 | 20 / 20 |
| operating-system/Chapter 3_Processes.pdf | 61 | 12.05% / 12.05% / 10.22% | 145 / 145 / 0 | 0.820 / 2.079 / 1.378 | 32 / 32 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 47 | 10.11% / 10.11% / 9.53% | 124 / 124 / 0 | 0.787 / 1.958 / 1.268 | 25 / 25 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 62 | 26.78% / 19.83% / 20.35% | 427 / 429 / 18 | 0.880 / 2.271 / 1.713 | 34 / 34 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 62 | 17.26% / 16.50% / 16.14% | 398 / 279 / 119 | 1.045 / 2.659 / 1.906 | 33 / 33 |

## Fonts, bullets, code and reading order

| Diagnostic | Plain | Original | Revised |
| --- | ---: | ---: | ---: |
| Vietnamese combining marks after NFD, calculus | 21,773 | 21,773 | 21,773 |
| Bullet-prefixed lines, OS | 482 | 1,084 | 1,102 |
| All indented lines (code, table cells and prose), OS | 226 | 0 | 116 |
| Exact source code lines preserved in order, OS | 535 / 605 | 602 / 605 | 605 / 605 |

Per-file preservation counts (P/L/R); mappings are original-rule changed characters
in Symbol/dingbat/large-bracket fonts. VNI counts are zero in every file.

| PDF | Diacritics P/L/R | Bullets P/L/R | All indented lines P/L/R | Code lines in order P/L/R (source count) | Mappings S/D/B |
| --- | --- | --- | --- | --- | --- |
| calculus 1/1. Giới hạn dãy số.pdf | 4674 / 4674 / 4674 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 0 |
| calculus 1/2. Giới hạn và liên tục hàm một biến.pdf | 3672 / 3672 / 3672 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 15 |
| calculus 1/3. Đạo hàm và vi phân.pdf | 5657 / 5657 / 5657 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 8 |
| calculus 1/4. Tích phân xác định.pdf | 1300 / 1300 / 1300 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 6 |
| calculus 1/5. Tích phân bất định.pdf | 3004 / 3004 / 3004 | 0 / 0 / 0 | 14 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 22 |
| calculus 1/6. Tích phân suy rộng.pdf | 3466 / 3466 / 3466 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 0 |
| operating-system/Chapter 2_Operating System Structures.pdf | 0 / 0 / 0 | 0 / 159 / 159 | 0 / 0 / 0 | 0 / 8 / 8 (8) | 0 / 159 / 0 |
| operating-system/Chapter 3_Processes.pdf | 3 / 0 / 0 | 115 / 254 / 268 | 4 / 0 / 0 | 188 / 192 / 192 (192) | 0 / 139 / 0 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 0 / 0 / 0 | 65 / 189 / 192 | 0 / 0 / 0 | 68 / 76 / 76 (76) | 0 / 124 / 0 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 0 / 0 / 0 | 161 / 272 / 272 | 79 / 0 / 25 | 40 / 43 / 44 (44) | 20 / 272 / 0 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 0 / 0 / 0 | 141 / 210 / 211 | 143 / 0 / 91 | 239 / 283 / 285 (285) | 0 / 204 / 0 |

Plain code matching uses the font-decoded source text as its reference; a plain
line containing a Symbol glyph may therefore fail matching. This metric checks
that authored code text survives in sequence; it does not prove semantic code
correctness or that every code indentation level is reconstructed. Indented-line
counts include every line starting with whitespace, not only code. Revised lines
keep indentation only when they have a monospaced span, which can include monospaced
table cells. All calculus
text diacritics survive. The three marks removed from OS text accompany decorative
font remapping, not Vietnamese prose.

Original font mappings hit 20 Symbol spans / 20 characters and 898 dingbat spans /
898 characters. There are no VNI font characters or legacy VNI decoding hits in the
current corpus. The original large-bracket mapping touches 51 characters. Font
counts describe original-rule mapping activity, not ground-truth accuracy.

Visually checked OS CPU Scheduling page 21's process/burst-time table and page 33's
two-column Pthread code against the revised text. Table rows remain `P_1 24`,
`P_2 3`, `P_3 3`; includes precede the main body and the closing brace follows it,
with indentation retained. The page 21 Gantt chart still has fragmented labels
and authored object order rather than a reconstructed timeline. Page 33's extracted
`NUM THREADS` differs from the rendered `NUM_THREADS`, already present in the PDF
text layer. These checks do not claim lossless figure extraction. Synthetic tests
also cover identical table cells, adjacent text runs, overlapping footer/code order,
numbered code lines and heading detection without changing source order.

## Keep/remove decisions

Formula rules stay because calculus is in scope. Two slide heuristics were narrowed
so they do not touch formulas. A prose word in a numerator does not block fraction
rebuilding, so `(arctan x)/x` stays one fraction. Touching blocks are grouped as in
the original rules, except that a code block is not grouped with a touching footer
or caption unless one of them is math-like; that keeps listing order. A bullet or a
code line sends a math-like region to ordinary line reading only when the region
has no drawn rules. OS lines such as `• 0 ≤ t ≤ d ≤ p` keep their line reading. A
bulleted fraction over a drawn bar is still rebuilt (`• y = (a + 1)/b`). That last
condition left all 11 PDFs byte-identical to the previous revision.

| Feature | Decision | Current corpus ablation against original rules |
| --- | --- | --- |
| Symbol/dingbat remapping | Keep | 20 Symbol and 898 dingbat characters remapped |
| VNI decoder | Keep, explicit owner decision D1 | No VNI hits; behavioral tests cover legacy and already-Unicode text |
| Radical geometry | Keep, calculus in scope | Zero changed pages |
| Large-bracket reconstruction | Keep, calculus in scope | Removing it changes 10 pages, 16 non-whitespace characters, +6 fragments |
| Vertical/absolute-value bars | Keep, calculus in scope | Zero changed pages |
| Limit/big-operator attachment | Keep, calculus in scope | Removing it changes 126 pages, 1,365 characters, −4 fragments |
| Region joining, scripts and horizontal fractions | Keep | Without strokes, 0 OS pages change; calculus remains formula-sensitive |

Removing all formula-specific features together would change 131 current pages /
1,379 characters. With them kept, revised calculus text is identical to the
original rules on every page. VNI decoding is kept per D1 rather than inferred
unnecessary from an absent corpus hit.

## OCR warnings and source boundaries

Sparse pages still attempt OCR. Every OCR exception or empty result is logged with
the one-based page. A short readable text layer survives an OCR failure without a
user warning (D3). Only a page left entirely empty carries `ocr_failed` metadata
through the pipeline into `documents.extraction_warnings`, the list API and the
course UI. Warning-only units never become retrieval text or alter title-frequency
calculation. An entirely unreadable document saves warnings before failing clearly.
Migration `0022_extraction_warnings` follows `0021_remove_textbook_scope`.

No current corpus page triggered sparse-page OCR or a warning, so this corpus
cannot compare OCR recognition quality. `vie+eng` / 200 dpi follows the owner's
October 22 shared-setting measurement. OCR regression tests exercise exception,
empty output, short-text fallback and the exact language/rendering settings.
The measurement cache keys include the language order and rendered image bytes.

Chunking now skips units with empty text for every reader, not only PDFs; the
other chunking rules and page-number locators are unchanged. Every file has identical
covered source pages before/after, with no invented page numbers. Total chunks
are 420 → 420 (calculus 276 → 276; OS 144 → 144). Calculus chunk content and
locators are identical to the original reader. OS chunk text changes where code,
bullets and reading order are repaired, and in leading whitespace. Warning-only
pages are explicitly excluded from chunk locators.

## Retrieval replay

The current replay uses all 11 PDFs and 12 fixed, hand-labelled prose probes
(six per subject, embedded in `replay_pdf_retrieval.py`). Gold chunks are determined
by filename and one-based PDF page, not historical database UUIDs. It reuses
`backend/scripts/benchmark_rerank.py` ingestion, ranking and metric functions.
Qwen3 Embedding 0.6B (768 dimensions, last-token pooling) and BAAI bge-reranker-base
are unchanged, with pinned model SHAs in the commands below. Top-k is 10 and the
existing hybrid/rerank candidate budget is unchanged. No generated queries, fake
embeddings or substitute reranker scores are used. These few probes check usable
content and locators, not statistically significant retrieval or formula accuracy.

The final reader, including the whitespace trimming above, was replayed on
2026-10-26 with the same probes, pinned models and retrieval parameters as the
recorded original-reader replay. Both index 420 chunks (calculus 276, OS 144).
Every probe has the same rank in every mode and the same gold-chunk count, so every metric below is identical
for the original and final readers. Dense calculus Recall@1 is 0.833 for both.

Each cell is original = final (measured).

| Mode / subject | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
| --- | --- | --- | --- | --- | --- | --- |
| dense / all | 0.583 | 0.833 | 0.917 | 1.000 | 0.727 | 0.794 |
| dense / calculus 1 | 0.833 | 0.833 | 1.000 | 1.000 | 0.875 | 0.905 |
| dense / operating-system | 0.333 | 0.833 | 0.833 | 1.000 | 0.579 | 0.683 |
| hybrid / all | 0.500 | 0.833 | 0.833 | 1.000 | 0.665 | 0.746 |
| hybrid / calculus 1 | 0.333 | 0.833 | 0.833 | 1.000 | 0.556 | 0.665 |
| hybrid / operating-system | 0.667 | 0.833 | 0.833 | 1.000 | 0.774 | 0.827 |
| hybrid+rerank / all | 0.583 | 1.000 | 1.000 | 1.000 | 0.778 | 0.835 |
| hybrid+rerank / calculus 1 | 0.500 | 1.000 | 1.000 | 1.000 | 0.722 | 0.794 |
| hybrid+rerank / operating-system | 0.667 | 1.000 | 1.000 | 1.000 | 0.833 | 0.877 |

Probe numbers follow the fixed order in `PROBES` in the replay script. Each rank is
the same for the original and final readers.

| Probe / source page | Dense | Hybrid | Reranked | Gold chunks |
| --- | --- | --- | --- | --- |
| Q1: calculus 1/1. Giới hạn dãy số.pdf:4 | 1 | 1 | 1 | 1 |
| Q2: calculus 1/2. Giới hạn và liên tục hàm một biến.pdf:8 | 1 | 1 | 1 | 1 |
| Q3: calculus 1/3. Đạo hàm và vi phân.pdf:8 | 1 | 6 | 1 | 1 |
| Q4: calculus 1/4. Tích phân xác định.pdf:4 | 1 | 3 | 3 | 2 |
| Q5: calculus 1/5. Tích phân bất định.pdf:4 | 1 | 2 | 2 | 2 |
| Q6: calculus 1/6. Tích phân suy rộng.pdf:4 | 4 | 3 | 2 | 1 |
| Q7: operating-system/Chapter 2_Operating System Structures.pdf:4 | 1 | 1 | 1 | 1 |
| Q8: operating-system/Chapter 3_Processes.pdf:8 | 1 | 1 | 2 | 1 |
| Q9: operating-system/Chapter 4_Threads & Concurency.pdf:8 | 2 | 2 | 2 | 1 |
| Q10: operating-system/Chapter 5_CPU Scheduling.pdf:8 | 7 | 7 | 1 | 1 |
| Q11: operating-system/Chapter 5_CPU Scheduling.pdf:20 | 2 | 1 | 1 | 1 |
| Q12: operating-system/Chapter 6_Synchronization Tools.pdf:8 | 3 | 1 | 1 | 1 |

## Validation

| Check (2026-10-26) | Result |
| --- | --- |
| Full backend suite on a fresh private database, before the formula rules were restored | 1,158 passed; one live-LLM test deselected |
| Focused PDF layout, slide-layout, OCR-warning and document-reader tests, right after restoring the formula rules | 55 passed |
| Full backend suite on a fresh private database, final code | 1,200 passed; one live-LLM test deselected |
| PDF layout, slide-layout and OCR-warning unit tests, final code | 57 passed |
| Extraction measurement, final code | Calculus identical to the original rules on 411 / 411 pages; OS source code lines 605 / 605 in order |
| Retrieval replay of the final reader, 12 probes, pinned models | Every rank and metric identical to the original reader |
| Course document-page frontend tests, final code | 49 passed |
| Ruff on backend and measurement scripts, whitespace checks | Passed |

The backend suite covers persistence through finished-job payload cleanup, API
warnings, an unreadable upload failing through the worker's error handling with its
Vietnamese reason in `failures[].error`, chunk locators and migration ordering. No
browser or live-model generation test was run during this refresh.

## Reproduce the current measurements

Run from the repository root with the existing backend environment. The corpus is
ignored by git; `PDFS` must point to its current copy. Raw outputs go outside git.
Commands below create only private, throwaway containers; remove them afterwards.
Set `EVIDENCE_DIR` to a private output directory, `PDFS` to the corpus copy, and
choose free local ports for `DB_PORT`, `EMBED_PORT` and `RERANK_PORT`.

```sh
EVIDENCE_DIR=<private-evidence-dir>
PDFS=<corpus-dir>
DB_PORT=<db-port> EMBED_PORT=<embedder-port> RERANK_PORT=<reranker-port>
BASELINE=760483b9825d646d785e78ef3c56989998142088

docker run --rm -d --name pdf-ocr --entrypoint sleep do-an-backend:latest infinity
backend/.venv/bin/python docs/measurements/measure_pdf_reader.py \
  --documents "$PDFS" --baseline-ref "$BASELINE" --output "$EVIDENCE_DIR/extraction" \
  --ocr-container pdf-ocr --ocr-cache "$EVIDENCE_DIR/ocr-cache"
backend/.venv/bin/python docs/measurements/ablate_pdf_repairs.py \
  --documents "$PDFS" --baseline-ref "$BASELINE" --output "$EVIDENCE_DIR/ablations.json"

docker run --rm -d --name pdf-db \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=microlearning \
  -p 127.0.0.1:$DB_PORT:5432 pgvector/pgvector:pg16
docker run --rm -d --name pdf-embedder --cpus 2 \
  -p 127.0.0.1:$EMBED_PORT:80 -v "$EVIDENCE_DIR/embedder-models:/data" \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4 \
  --model-id onnx-community/Qwen3-Embedding-0.6B-ONNX \
  --revision c25a394dd583836952667c12f008335071b3f43d \
  --pooling last-token --max-batch-tokens 1024 --max-client-batch-size 128
docker run --rm -d --name pdf-reranker --cpus 2 \
  -p 127.0.0.1:$RERANK_PORT:80 -v "$EVIDENCE_DIR/reranker-models:/data" \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4 \
  --model-id BAAI/bge-reranker-base \
  --revision 2cfc18c9415c912f9d8155881c133215df768a70 --max-client-batch-size 64
# Wait for PostgreSQL and both model /health endpoints.
export APP_RUNTIME_DATABASE_URL=postgresql+psycopg://app_runtime:app_runtime_dev_password@127.0.0.1:$DB_PORT/microlearning
export APP_WORKER_DATABASE_URL=postgresql+psycopg://app_worker:app_worker_dev_password@127.0.0.1:$DB_PORT/microlearning
export SECRET_KEY=<test-secret-of-at-least-32-characters>
export LLM_GATEWAY_URL=http://127.0.0.1:$EMBED_PORT/v1
export LLM_GATEWAY_KEY=<local-placeholder>
export EMBEDDING_MODEL=Qwen3-Embedding-0.6B
export RERANKER_URL=http://127.0.0.1:$RERANK_PORT
for db in replay_before replay_after; do
  docker exec pdf-db createdb -U postgres "$db"
  export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:$DB_PORT/$db"
  (cd backend && .venv/bin/alembic upgrade head)
done
export BENCHMARK_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:$DB_PORT/replay_before
backend/.venv/bin/python docs/measurements/replay_pdf_retrieval.py \
  --documents "$PDFS" --reader-ref "$BASELINE" --ocr-container pdf-ocr \
  --cache "$EVIDENCE_DIR/ocr-cache" --output "$EVIDENCE_DIR/retrieval-before.json"
export BENCHMARK_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:$DB_PORT/replay_after
backend/.venv/bin/python docs/measurements/replay_pdf_retrieval.py \
  --documents "$PDFS" --ocr-container pdf-ocr \
  --cache "$EVIDENCE_DIR/ocr-cache" --output "$EVIDENCE_DIR/retrieval-after.json"
export DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:$DB_PORT/microlearning
(cd backend && .venv/bin/alembic upgrade head && .venv/bin/pytest -m 'not live_llm' -q)
(cd backend && .venv/bin/ruff check . ../docs/measurements/*.py)
git diff --check
docker stop pdf-db pdf-ocr pdf-embedder pdf-reranker
```

Only real model vectors are cached by text in the evidence directory. Use a fresh
vector cache if changing models; this replay intentionally keeps models fixed.
