# PDF reader fit — refreshed 2026-10-26

The current corpus contains 11 PDFs / 681 pages: six calculus 1 PDFs (411 pages)
and five operating-system PDFs (270 pages). Advanced programming and scientific
socialism are PPTX; machine learning is audio/video. Only PDFs are measured here.
This replaces the earlier four-subject corpus measurement as the current evidence.
The owner made this corpus the reference source on 2026-10-26. Calculus 1 is
therefore in scope again, and the formula rules stay: limit/big-operator attachment,
large brackets, radicals, absolute-value bars and math-region rebuilding. Formula and
figure extraction improvements beyond the original rules are a separate follow-up.

Owner decisions applied: retain the VNI decoder; retain only this summary and the
measurement scripts in git; warn on a document only when both its text layer and
OCR leave a page completely empty; use Tesseract `vie+eng` at 200 dpi.
Raw per-file JSON, page text diffs, model responses and logs stay under the task's
private evidence directory `/mnt/d/firstmate/data/do-an-r53-pdf-reader-fit/`.

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
and reading-order repairs described below.

| Subject | Pages | Lines P/L/R | Fragments P/L/R | Fragment rate P/L/R | ms/page P/L/R |
| --- | ---: | --- | --- | --- | --- |
| calculus 1 | 411 | 9326 / 5564 / 5564 | 2975 / 585 / 585 | 31.90% / 10.51% / 10.51% | 4.679 / 10.109 / 10.802 |
| operating-system | 270 | 3440 / 3381 / 3366 | 587 / 501 / 486 | 17.06% / 14.82% / 14.44% | 0.801 / 2.061 / 1.464 |

| PDF | Pages | Fragment rate P/L/R | Changed chars P→L / P→R / L→R | ms/page P/L/R | Chunks L/R |
| --- | ---: | --- | --- | --- | --- |
| calculus 1/1. Giới hạn dãy số.pdf | 77 | 31.30% / 6.07% / 6.07% | 2933 / 2933 / 0 | 6.958 / 9.970 / 10.587 | 52 / 52 |
| calculus 1/2. Giới hạn và liên tục hàm một biến.pdf | 54 | 23.66% / 6.38% / 6.38% | 1334 / 1334 / 0 | 4.444 / 10.324 / 14.562 | 37 / 37 |
| calculus 1/3. Đạo hàm và vi phân.pdf | 107 | 23.93% / 6.84% / 6.84% | 3319 / 3319 / 0 | 6.443 / 14.726 / 14.527 | 74 / 74 |
| calculus 1/4. Tích phân xác định.pdf | 36 | 32.71% / 18.33% / 18.33% | 602 / 602 / 0 | 1.688 / 4.829 / 4.790 | 20 / 20 |
| calculus 1/5. Tích phân bất định.pdf | 68 | 38.84% / 9.03% / 9.03% | 2733 / 2733 / 0 | 2.717 / 7.766 / 7.887 | 48 / 48 |
| calculus 1/6. Tích phân suy rộng.pdf | 69 | 39.48% / 20.54% / 20.54% | 2145 / 2145 / 0 | 3.076 / 7.998 / 8.334 | 45 / 45 |
| operating-system/Chapter 2_Operating System Structures.pdf | 38 | 9.64% / 9.64% / 9.64% | 159 / 159 / 0 | 0.825 / 2.008 / 1.307 | 20 / 20 |
| operating-system/Chapter 3_Processes.pdf | 61 | 12.05% / 12.05% / 10.22% | 145 / 145 / 0 | 0.767 / 1.955 / 1.326 | 32 / 32 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 47 | 10.11% / 10.11% / 9.53% | 124 / 124 / 0 | 0.756 / 1.883 / 1.214 | 25 / 25 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 62 | 26.78% / 19.83% / 20.35% | 427 / 429 / 18 | 0.800 / 2.157 / 1.616 | 34 / 34 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 62 | 17.26% / 16.50% / 16.14% | 398 / 279 / 119 | 0.853 / 2.240 / 1.731 | 33 / 33 |

## Fonts, bullets, code and reading order

| Diagnostic | Plain | Original | Revised |
| --- | ---: | ---: | ---: |
| Vietnamese combining marks after NFD, calculus | 21,773 | 21,773 | 21,773 |
| Bullet-prefixed lines, OS | 482 | 1,084 | 1,102 |
| Explicitly indented lines, OS | 226 | 0 | 195 |
| Exact source code lines preserved in order, OS | 535 / 605 | 602 / 605 | 605 / 605 |

Per-file preservation counts (P/L/R); mappings are original-rule changed characters
in Symbol/dingbat/large-bracket fonts. VNI counts are zero in every file.

| PDF | Diacritics P/L/R | Bullets P/L/R | Indented lines P/L/R | Code lines in order P/L/R (source count) | Mappings S/D/B |
| --- | --- | --- | --- | --- | --- |
| calculus 1/1. Giới hạn dãy số.pdf | 4674 / 4674 / 4674 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 0 |
| calculus 1/2. Giới hạn và liên tục hàm một biến.pdf | 3672 / 3672 / 3672 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 15 |
| calculus 1/3. Đạo hàm và vi phân.pdf | 5657 / 5657 / 5657 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 8 |
| calculus 1/4. Tích phân xác định.pdf | 1300 / 1300 / 1300 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 6 |
| calculus 1/5. Tích phân bất định.pdf | 3004 / 3004 / 3004 | 0 / 0 / 0 | 14 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 22 |
| calculus 1/6. Tích phân suy rộng.pdf | 3466 / 3466 / 3466 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 (0) | 0 / 0 / 0 |
| operating-system/Chapter 2_Operating System Structures.pdf | 0 / 0 / 0 | 0 / 159 / 159 | 0 / 0 / 0 | 0 / 8 / 8 (8) | 0 / 159 / 0 |
| operating-system/Chapter 3_Processes.pdf | 3 / 0 / 0 | 115 / 254 / 268 | 4 / 0 / 4 | 188 / 192 / 192 (192) | 0 / 139 / 0 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 0 / 0 / 0 | 65 / 189 / 192 | 0 / 0 / 0 | 68 / 76 / 76 (76) | 0 / 124 / 0 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 0 / 0 / 0 | 161 / 272 / 272 | 79 / 0 / 48 | 40 / 43 / 44 (44) | 20 / 272 / 0 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 0 / 0 / 0 | 141 / 210 / 211 | 143 / 0 / 143 | 239 / 283 / 285 (285) | 0 / 204 / 0 |

Plain code matching uses the font-decoded source text as its reference; a plain
line containing a Symbol glyph may therefore fail matching. This metric checks
that authored code text survives in sequence; it does not prove semantic code
correctness or that every code indentation level is reconstructed. All calculus
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

The original October 11 corpus had 48 files / 2,145 pages across DSA, OS,
program fundamentals and web programming. Removing the whole reconstruction path
added 580 short-line fragments across 243 changed pages. This supported retaining
region joining and ordinary superscript/subscript repairs for algorithm indices
and fragmented code. Horizontal fraction bars repaired two useful slides (DSA
search-hash page 13 and OS Virtual Memory page 46). Code indentation, baseline run
joining and overlap-aware shadow deduplication remain useful: current OS code
retention rises from 602 to 605 of 605 lines, with no OS stroke-ablation changes.

An earlier revision removed the formula-specific rules because the original corpus
had no formula-heavy subject. On the current corpus that revision raised the
calculus fragment rate from 10.51% to 11.02%. Dense calculus Recall@1 fell from
0.833 to 0.667. Calculus is in scope again, so those rules are kept. Two slide
heuristics from that revision were narrowed for the same reason. First, a prose
word in a numerator no longer blocks fraction rebuilding: that guard split real
fractions such as `(arctan x)/x` and "Tổng hữu hạn các VCB" over its denominator
on 30 calculus pages. Second, touching blocks are grouped as in the original rules,
except that a code block is not grouped with a touching footer or caption unless
one of them is math-like. That keeps listing order without separating formula pieces.

| Feature | Decision | Original corpus evidence | Current corpus ablation against original rules |
| --- | --- | --- | --- |
| Symbol/dingbat remapping | Keep | 68 Symbol mapping spans / 69 characters | 20 Symbol and 898 dingbat characters remapped |
| VNI decoder | Keep, explicit owner decision D1 | 37 already-Unicode VNI-font characters; zero legacy decoding hits | No VNI hits; behavioral tests cover legacy and already-Unicode text |
| Radical geometry | Keep, calculus in scope | Zero changed pages | Zero changed pages |
| Large-bracket reconstruction | Keep, calculus in scope | Zero encoded bracket hits; two table pages changed by border artifacts | Removing it changes 10 pages, 16 non-whitespace characters, +6 fragments |
| Vertical/absolute-value bars | Keep, calculus in scope | Two recursion table pages, five characters from table borders | Zero changed pages |
| Limit/big-operator attachment | Keep, calculus in scope | One self-adjusting page, 26 characters; incorrect `min` attachment | Removing it changes 126 pages, 1,365 characters, −4 fragments |
| Region joining, scripts and horizontal fractions | Keep | 243 changed pages / 580 extra fragments without rebuilding; two useful fractions | Without strokes, 0 OS pages change; calculus remains formula-sensitive |

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

Chunking rules and page-number locators are unchanged. Every file has identical
covered source pages before/after, with no invented page numbers. Total chunks
are 420 → 420 (calculus 276 → 276; OS 144 → 144). Calculus chunk content and
locators are identical to the original reader. OS chunk text changes where code,
bullets and reading order are repaired. Warning-only pages are explicitly excluded
from chunk locators.

## Retrieval replay

The previous four-subject replay used 24 fixed probes and 1,214 → 1,217 chunks.
Dense and production hybrid+rerank ranks were unchanged; unreranked hybrid
Recall@1 fell 0.708 → 0.625 and MRR@10 0.810 → 0.758 on two probes. Those historical
results are not comparable to the replaced corpus.

The current replay uses all 11 PDFs and 12 fixed, hand-labelled prose probes
(six per subject, embedded in `replay_pdf_retrieval.py`). Gold chunks are determined
by filename and one-based PDF page, not historical database UUIDs. It reuses
`backend/scripts/benchmark_rerank.py` ingestion, ranking and metric functions.
Qwen3 Embedding 0.6B (768 dimensions, last-token pooling) and BAAI bge-reranker-base
are unchanged, with pinned model SHAs in the commands below. Top-k is 10 and the
existing hybrid/rerank candidate budget is unchanged. No generated queries, fake
embeddings or substitute reranker scores are used. These few probes check usable
content and locators, not statistically significant retrieval or formula accuracy.

Each cell is the original-reader replay (420 chunks). Retrieval is scoped per
course, and the replay caches real model vectors by chunk text. Calculus chunks are
now identical to the original reader, so the calculus rows apply unchanged to the
revised reader: dense calculus Recall@1 stays 0.833. The replay was not rerun after
the formula rules were restored. The earlier revision's OS chunks gave the same
ranks as the original on every OS probe in every mode. The current OS text differs
from that revision only by line order on nine pages (page numbers and Gantt-chart
labels in CPU Scheduling and Synchronization Tools). No OS rank is claimed for the
current revision beyond that.

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

Probe numbers follow the fixed order in `PROBES` in the replay script. "Removed"
is the superseded revision without formula rules. Calculus ranks for the current
revision equal the original column.

| Probe / source page | Dense original/removed | Hybrid original/removed | Reranked original/removed | Gold chunks |
| --- | --- | --- | --- | --- |
| Q1: calculus 1/1. Giới hạn dãy số.pdf:4 | 1/1 | 1/1 | 1/1 | 1 |
| Q2: calculus 1/2. Giới hạn và liên tục hàm một biến.pdf:8 | 1/1 | 1/1 | 1/1 | 1 |
| Q3: calculus 1/3. Đạo hàm và vi phân.pdf:8 | 1/1 | 6/5 | 1/1 | 1 |
| Q4: calculus 1/4. Tích phân xác định.pdf:4 | 1/2 | 3/3 | 3/3 | 2 |
| Q5: calculus 1/5. Tích phân bất định.pdf:4 | 1/1 | 2/3 | 2/2 | 2 |
| Q6: calculus 1/6. Tích phân suy rộng.pdf:4 | 4/4 | 3/3 | 2/2 | 1 |
| Q7: operating-system/Chapter 2_Operating System Structures.pdf:4 | 1/1 | 1/1 | 1/1 | 1 |
| Q8: operating-system/Chapter 3_Processes.pdf:8 | 1/1 | 1/1 | 2/2 | 1 |
| Q9: operating-system/Chapter 4_Threads & Concurency.pdf:8 | 2/2 | 2/2 | 2/2 | 1 |
| Q10: operating-system/Chapter 5_CPU Scheduling.pdf:8 | 7/7 | 7/7 | 1/1 | 1 |
| Q11: operating-system/Chapter 5_CPU Scheduling.pdf:20 | 2/2 | 1/1 | 1/1 | 1 |
| Q12: operating-system/Chapter 6_Synchronization Tools.pdf:8 | 3/3 | 1/1 | 1/1 | 1 |

The superseded revision worsened dense Q4 (definite-integral construction) from
rank 1 to 2 and moved unreranked hybrid Q3/Q5. Restoring the formula rules
returns all calculus ranks to the original values without changing embedding or
reranker models.


## Validation and prior live evidence

| Check (2026-10-26) | Result |
| --- | --- |
| Full backend suite on a fresh private database, before the formula rules were restored | 1,158 passed; one live-LLM test deselected |
| Focused PDF layout, slide-layout, OCR-warning and document-reader tests after restoring the formula rules | 55 passed |
| Extraction measurement after restoring the formula rules | Calculus identical to the original rules on 411 / 411 pages |
| Course document-page frontend tests | 49 passed |
| Ruff on changed Python files, whitespace checks | Passed after the restore |

The backend suite covers persistence through finished-job payload cleanup, API
warnings, unreadable uploads, chunk locators and migration ordering. The final
focused run covers the additional exact OCR language/render check. No new browser
or live-model generation test was run during this refresh.

Historical private HTTP smoke (October 11) used separate DB/backend/frontend/model
services. A mixed readable/unreadable upload completed with a persistent page-2
warning after job payload cleanup; an entirely unreadable upload failed with both
page warnings. Browser bridge/CDP access failed, so no passing browser screenshot
is claimed. Frontend DOM tests covered persistent warnings. Those live scenarios
were already driven and recorded; pipeline Test should run automated suites and
one short changed-flow smoke rather than rebuilding and repeating the entire stack.

## Reproduce the current measurements

Run from the repository root with the existing backend environment. The corpus is
ignored by git; `PDFS` must point to its current copy. Raw outputs go outside git.
Commands below create only private task containers; remove them afterwards.

```sh
TASK_DATA=/mnt/d/firstmate/data/do-an-r53-pdf-reader-fit
PDFS=/mnt/d/firstmate/projects/do-an/docs/documents
BASELINE=760483b9825d646d785e78ef3c56989998142088

docker run --rm -d --name fm-r53-oct26-ocr --entrypoint sleep do-an-backend:latest infinity
backend/.venv/bin/python docs/measurements/measure_pdf_reader.py \
  --documents "$PDFS" --baseline-ref "$BASELINE" --output "$TASK_DATA/oct26" \
  --ocr-container fm-r53-oct26-ocr --ocr-cache "$TASK_DATA/oct26-ocr-cache"
backend/.venv/bin/python docs/measurements/ablate_pdf_repairs.py \
  --documents "$PDFS" --baseline-ref "$BASELINE" --output "$TASK_DATA/oct26-ablations.json"

docker run --rm -d --name fm-r53-oct26-db \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=microlearning \
  -p 127.0.0.1:55453:5432 pgvector/pgvector:pg16
docker run --rm -d --name fm-r53-oct26-embedder --cpus 2 \
  -p 127.0.0.1:58453:80 -v "$TASK_DATA/embedder-models:/data" \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4 \
  --model-id onnx-community/Qwen3-Embedding-0.6B-ONNX \
  --revision c25a394dd583836952667c12f008335071b3f43d \
  --pooling last-token --max-batch-tokens 1024 --max-client-batch-size 128
docker run --rm -d --name fm-r53-oct26-reranker --cpus 2 \
  -p 127.0.0.1:58454:80 -v "$TASK_DATA/reranker-models:/data" \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4 \
  --model-id BAAI/bge-reranker-base \
  --revision 2cfc18c9415c912f9d8155881c133215df768a70 --max-client-batch-size 64
# Wait for PostgreSQL and both model /health endpoints.
export APP_RUNTIME_DATABASE_URL=postgresql+psycopg://app_runtime:app_runtime_dev_password@127.0.0.1:55453/microlearning
export APP_WORKER_DATABASE_URL=postgresql+psycopg://app_worker:app_worker_dev_password@127.0.0.1:55453/microlearning
export SECRET_KEY=r53-test-secret-with-at-least-32-characters
export LLM_GATEWAY_URL=http://127.0.0.1:58453/v1
export LLM_GATEWAY_KEY=task-private
export EMBEDDING_MODEL=Qwen3-Embedding-0.6B
export RERANKER_URL=http://127.0.0.1:58454
for db in r53_oct26_before r53_oct26_after; do
  docker exec fm-r53-oct26-db createdb -U postgres "$db"
  export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:55453/$db"
  (cd backend && .venv/bin/alembic upgrade head)
done
export BENCHMARK_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55453/r53_oct26_before
backend/.venv/bin/python docs/measurements/replay_pdf_retrieval.py \
  --documents "$PDFS" --reader-ref "$BASELINE" --ocr-container fm-r53-oct26-ocr \
  --cache "$TASK_DATA/oct26-ocr-cache" --output "$TASK_DATA/oct26-retrieval-before.json"
export BENCHMARK_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55453/r53_oct26_after
backend/.venv/bin/python docs/measurements/replay_pdf_retrieval.py \
  --documents "$PDFS" --ocr-container fm-r53-oct26-ocr \
  --cache "$TASK_DATA/oct26-ocr-cache" --output "$TASK_DATA/oct26-retrieval-after.json"
export DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55453/microlearning
(cd backend && .venv/bin/alembic upgrade head && .venv/bin/pytest -m 'not live_llm' -q)
backend/.venv/bin/ruff check backend docs/measurements/*.py
git diff --check
docker stop fm-r53-oct26-db fm-r53-oct26-ocr fm-r53-oct26-embedder fm-r53-oct26-reranker
```

Only real model vectors are cached by text in the task directory. Use a fresh
vector cache if changing models; this replay intentionally keeps models fixed.
