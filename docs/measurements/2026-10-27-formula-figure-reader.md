# Formula and figure reading — 2026-10-27

Follow-up to the 2026-10-26 baseline on the standard corpus (`docs/documents`): PDF
formulas lost radicals, fractions and sub/superscripts, and text inside pictures,
diagrams and SmartArt never reached the chunks. This note records the fixed checks,
before/after extraction on every affected file and the model spend.

## What changed

- **TeX formulas, deterministic.** All six calculus PDFs are TeX Beamer. Their CMEX and
  msbm fonts carry no ToUnicode map, so integrals came out as `R`/`Z`, radicals as
  `p`/`q`, big brackets as control characters and ℝ/ℕ as `R`/`N`. The codes are now
  mapped, CMEX glyphs get their real depth below the baseline (a big bracket spans the
  fraction it encloses), and a TeX radical (a `√` glyph plus a bar starting at its right
  edge) is rebuilt like a MathType one.
- **Selective formula re-read.** `suspect_formula_lines` scores only lines from math
  regions: unbalanced or empty brackets, operators hanging at a bracket, or two or more
  stray limit/script fragments. A flagged page is rendered at 150 dpi and sent once,
  through the gateway (`FORMULA_VISION_MODEL`, default `gemini-fast`), for a line-by-line
  transcription with LaTeX formulas. It replaces the page text only when ≥ 90 % of the
  text-layer words are present and ≤ 10 % of its words are new; otherwise the page keeps
  the rebuilt text with a `formula_unverified` warning shown on the course page. Locators
  keep the page; `extraction_method` is `vision`.
- **Figures.** Pictures covering ≥ 4 % of a slide/page and appearing on fewer than three
  slides/pages are OCRed with Tesseract `vie+eng` (already in the backend image), four at a
  time. An image counts as text only with ≥ 3 words at confidence ≥ 60 and a mean word
  confidence ≥ 60; photos, logos and cluttered screenshots give nothing. Text already in
  the text layer is not repeated. The result is added as `Hình: …` where the picture sits
  in the slide, or at the end of the PDF page. No model is called for figures.
- **SmartArt.** Its text lives in the diagram data part, not the slide; it is now read.

## Method

Before is `8e52783` (main), after is this branch. Both ran `read_document` on the same
files inside throw-away `do-an-backend` containers (Tesseract present) with a private
gateway container; chunk counts use `chunk_units`. Seconds are the wall-clock extract
step only. Embedding was not re-run; it scales with the chunk count (+0–4 per file).
Re-read latency is dominated by the free-tier rate limit: in the full run, 20 of 62 calls
were served by the `gemini-flash` fallback, and three files waited 79–110 s.

## Fixed checks

| Check | Before | After |
| --- | --- | --- |
| Giới hạn dãy số p39, problem | `I = lim_(n→∞) √(n^2 +)/(n + 1) √n/n.` | `I = lim_(n→∞) (√(n^2 + 1) − n)/(√(n + 1) − √n).` |
| p39, last step | `lim_(n→∞) (√n +_(√n))/(√(1 + 1/n2) + 1) = 0.` (q glyphs, lost `1/n`) | `lim_(n→∞) (√(1/n + 1/n^2) + √(1/n))/(√(1 + 1/n^2) + 1) = 0.` |
| Chap2_Inheritance slide 23 | title + exercise sentence only | + `Hình:` Point2D `-x:float = 0.0f`, `-y:float = 0.0f`, both constructors, `getX/setX/getY/setY/setXY/getXY/toString`; Point3D `-z:float = 0.0f`, both constructors, `getZ/setZ/setXYZ/getXYZ/toString`; `extends` |
| Socialism Chapter1 slide 9 | no unit | `Hình: KỶ NIỆM / 138 NĂM NGÀY QUỐC TẾ LAO ĐỘNG / (01/5/1886 - 01/5/2024)` |
| Socialism Chapter2 slides 31, 46, 56 | no unit | SmartArt text, e.g. slide 31 `có tính tổ chức kỷ luật cao / mang bản chất quốc tế / có tinh thần cách mạng triệt để` |

p39 is now rebuilt from the text layer and is not sent to the model.

## Per file

Extract seconds, units, chunks, re-read pages, `formula_unverified` warnings and
`Hình:` blocks. Diacritics count Vietnamese combining marks after NFD.

| File | Extract s before → after | Units | Chunks | Re-read pages | Warnings | Figures | Diacritics |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| advanced-programming/Chap1_Encapsulation.pptx | 0.09 → 1.13 | 29 → 29 | 16 → 17 | 0 | 0 | 4 | 2 → 6 |
| advanced-programming/Chap2_Inheritance.pptx | 0.08 → 1.25 | 29 → 29 | 16 → 18 | 0 | 0 | 4 | 2 → 6 |
| advanced-programming/Chap3_Polymorphism.pptx | 0.06 → 0.49 | 19 → 19 | 10 → 10 | 0 | 0 | 1 | 0 → 1 |
| advanced-programming/Chap4_Abstract_Interface.pptx | 0.08 → 0.43 | 21 → 21 | 11 → 11 | 0 | 0 | 1 | 8 → 10 |
| advanced-programming/Chap5_Design_Patterns.pptx | 0.07 → 1.20 | 30 → 30 | 16 → 18 | 0 | 0 | 9 | 5 → 18 |
| calculus 1/1. Giới hạn dãy số.pdf | 1.13 → 8.08 | 169 → 167 | 52 → 55 | 7 | 1 | 0 | 4674 → 4674 |
| calculus 1/2. Giới hạn và liên tục hàm một biến.pdf | 0.89 → 110.02 | 136 → 135 | 37 → 41 | 8 | 0 | 0 | 3672 → 3672 |
| calculus 1/3. Đạo hàm và vi phân.pdf | 1.58 → 78.66 | 266 → 262 | 74 → 78 | 14 | 5 | 0 | 5657 → 5652 |
| calculus 1/4. Tích phân xác định.pdf | 0.52 → 6.50 | 74 → 72 | 20 → 21 | 4 | 0 | 1 | 1300 → 1308 |
| calculus 1/5. Tích phân bất định.pdf | 0.92 → 5.01 | 172 → 167 | 48 → 50 | 5 | 0 | 0 | 3004 → 2999 |
| calculus 1/6. Tích phân suy rộng.pdf | 1.03 → 104.74 | 169 → 167 | 45 → 48 | 17 | 0 | 0 | 3466 → 3468 |
| operating-system/Chapter 2_Operating System Structures.pdf | 0.54 → 5.73 | 38 → 38 | 20 → 22 | 0 | 0 | 15 | 0 → 19 |
| operating-system/Chapter 3_Processes.pdf | 0.51 → 4.37 | 61 → 61 | 32 → 33 | 0 | 0 | 17 | 0 → 21 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 0.29 → 3.29 | 47 → 47 | 25 → 25 | 0 | 0 | 15 | 0 → 15 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 0.52 → 6.22 | 66 → 66 | 34 → 34 | 1 | 0 | 5 | 0 → 5 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 0.52 → 2.53 | 63 → 63 | 33 → 33 | 0 | 0 | 5 | 0 → 5 |
| scientific-socialism/Chapter 3.pptx | 0.08 → 2.12 | 38 → 38 | 19 → 19 | 0 | 0 | 2 | 1911 → 1929 |
| scientific-socialism/Chapter1.pptx | 0.22 → 3.63 | 50 → 51 | 26 → 26 | 0 | 0 | 3 | 2818 → 2833 |
| scientific-socialism/Chapter2.pptx | 0.16 → 3.19 | 55 → 58 | 28 → 30 | 0 | 0 | 3 | 3064 → 3364 |

## Regression checks

- **Calculus text layer.** The geometric changes touch 269 of 411 pages. Reviewed diffs
  are gains: ℝ/ℕ restored, `x_n` scripts become `x^n`/`x_(n+1)`, `(1 + 1/n)^n` keeps its
  bracket and exponent, radicals keep their content. On the 356 calculus pages that stay
  on the text layer, Vietnamese combining marks are identical to before (calculus 4's +8
  come from the OCRed GeoGebra plot on p10).
- **Re-read pages.** 62 pages were flagged (61 calculus, 1 OS), 56 accepted and 6 kept
  with a warning (Giới hạn dãy số p53; Đạo hàm và vi phân p34, 38, 66, 94, 96). On
  accepted pages the words that differ from the text layer are text-layer glitches the
  model split (`vẽđồthịhàm` → `vẽ đồ thị hàm`, `Rb` from a broken integral); diacritics
  on the remaining words are unchanged. Spot-checked p53 of Đạo hàm and of Tích phân suy
  rộng against the render: formulas match.
- **Operating systems.** Without the `Hình:` lines, OS text is byte-identical except CPU
  Scheduling p19 (the round-robin diagram's `q` labels triggered a re-read; its prose is
  unchanged and the labels become `$q$`, `$P_1$`).
- **PPTX.** Besides figures, SmartArt adds real outline text to other Chapter2 slides
  (2, 11, 12, 37, 43, 55, 58).

## Spend

Every model call went through the private gateway with the existing aliases. The two
smoke runs, two full runs and one partial re-run made 145 page re-reads; 137 recorded
190,899 prompt and 92,910 completion tokens (≈ 1.4 k prompt and 0.25–1 k completion per
page; Flash spends more), and the 8 of the first smoke run add about 13 k more.
`gemini-fast`/`gemini-flash` are the free-tier aliases; even billed at $0.50 per million
input and $3 per million output tokens the total would be about $0.40, under the $2 cap.

## Limits

- Formulas are linear text on rebuilt pages and LaTeX on re-read pages. Coordinates are
  the page locator; per-formula boxes are not stored.
- Sparse diagram labels below the confidence floor are dropped (CPU Scheduling p28, p37,
  p62), as are cluttered screenshots (Structures p10 Finder window). Screenshots that do
  pass, such as the phone home screen on Structures p9, add low-value text.
- Ingest of a heavily broken PDF now waits on the free-tier rate limit; 40 pages per
  document is the cap.
