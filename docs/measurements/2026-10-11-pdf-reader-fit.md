# PDF reader fit for the four retained subjects

Measured all 48 PDFs (2,145 pages) in dsa, operating-system, program-fundamentals,
and web-programming. The baseline is `760483b9825d646d785e78ef3c56989998142088`.
PDFs were opened read-only from a local copy of `docs/documents`; none is committed here.
File hashes and final measurements are in [pdf-reader-files.json](pdf-reader-files.json); the pre-change run is preserved in [pdf-reader-baseline-files.json](pdf-reader-baseline-files.json).

## Review correction status

The extraction, locator, retrieval and suite results below describe the pre-review
parser, including the rebase run. They are historical measurements, not final results
for the corrected listing joins and title normalization. These changes can alter chunks
and rankings; the old retrieval comparison does not establish current retrieval quality.
A full corpus and retrieval replay remains required before using these numbers to
characterize the corrected parser. Focused corpus evidence is recorded separately
in `pdf-reader-review-pages.json`.

## Method and limits

Compare plain `page.get_text("text")`, the original reader, and the revised reader
on every page. Timing is the sum of each page's median of three runs, divided by
pages, for text extraction only: no OCR, disk opening, chunking, embeddings or
reranking. The three main modes run in the same process on the same page.

A fragment is a nonblank line containing at most three non-whitespace characters.
Its rate is fragments / nonblank lines. This is a proxy: bullets, page numbers and
single-letter table cells can be legitimate short lines. Character changes use
`SequenceMatcher(autojunk=False)` after removing whitespace, counting the larger
of the replaced/inserted/deleted lengths per edit; this is not Levenshtein distance
or an accuracy score. Counts of NFD combining marks describe the extracted
characters, not a language accuracy score.

Font hits count spans whose text the original font decoder changes. Source code
lines are lines containing a PyMuPDF monospaced span; ordered substring matching
allows adjacent fragments to join and flags deletion/reordering. It also flags a
fraction deliberately reconstructed from monospaced numerator/denominator lines.
Explicit indentation means leading space/tab characters actually in the PDF,
not indentation inferred from glyph coordinates. Bullet counts are lines beginning
with a recognized bullet, so joining a detached bullet to text can change counts.

Two ablations in the main measurement isolate all stroke processing and all
region rebuilding (font-decoded source lines only). The latter's precomputed
line timing is not comparable with the main extraction timings. The separate
[ablate_pdf_repairs.py](ablate_pdf_repairs.py) disables each removed repair in
isolation and then together, using the original reader. Effects can overlap.

Complete reader/chunker comparisons use real Tesseract `eng+vie` for sparse pages,
cached by rendered PNG SHA-256. Blank OCR output is a recorded failure in the new
reader. The corpus has 67 sparse pages; two (CSS 43/44) produce empty OCR and now
carry warnings while retaining `text-decoration` / `text-transform` from the text layer.
All 48 source hashes match the pre-change measurement. No parser dependency, embedding model, reranker model or chunk token
threshold was changed.

## Extraction results

PyMuPDF 1.28.2; three repeats per page. Final paired extraction timings:

| Metric | Plain PyMuPDF | Original rules | Revised rules |
| --- | ---: | ---: | ---: |
| Characters in nonblank lines | 784,764 | 778,473 | 780,560 |
| Nonblank lines | 34,490 | 33,543 | 33,296 |
| Short-line fragments | 4,827 | 4,247 | 4,074 |
| Bullet-starting lines | 2,998 | 4,244 | 4,293 |
| Explicitly indented lines | 286 | 0 | 252 |
| NFD combining marks | 123 | 115 | 115 |
| Fragment rate | 14.00% | 12.66% | 12.24% |
| Milliseconds/page | 1.764 | 5.007 | 3.882 |
| Ordered source-code lines matched (of 2,148) | 2016 | 2101 | 2146 |

The revised reader takes 22.5% less extraction time than the original rules. The remaining two unmatched monospaced source lines are the numerator and denominator on search-hash page 13: they are rebuilt into the correct interpolation fraction, not deleted. The original reader also moved a closing brace before its code on CPU Scheduling page 33; the revised reader preserves it.

Plain → original changes 10,677 non-whitespace characters; plain → revised changes 7,915; original → revised changes 3,213 across 273 pages. These counts include ordering and repair punctuation, not only font conversion.

## Chunk boundaries and page locators

The heading/page grouping policy, token thresholds and overlap are unchanged. Empty warning units never participate in splitting or title-frequency calculation. Original page numbers remain one-based in source locators; all final chunk locators were checked against the source PDF page range. No previously covered source page is lost. The revised short-text fallback additionally covers CSS pages 43/44 when OCR returns no readable text.

Repaired text can cross existing token thresholds or stop looking like a numbered heading. The result is 1,214 → 1,217 chunks: recursion 69 → 68, self-adjusting 95 → 98, CSS 27 → 28; counts in all other files stay equal. Exact serialized locator multisets change in 14 files (83 removed, 86 added); these are not renumbered source pages. Comparing chunk indices with `zip` overcounts shifted boundaries (154 differing pairs), so the JSON also records locator multisets and per-file source-page coverage. This is not a claim that every old chunk byte range stays identical after text repairs.

## Per-file extraction table

P/O/R = plain/original/revised. S/D = Symbol/dingbat mapping spans (VNI and encoded bracket hits are zero throughout). Δchars is original → revised ignoring whitespace. Each timing is ms/page; each fragment rate uses that mode's nonblank-line denominator.

| PDF (under docs/documents) | Pages | Fragments % P/O/R | Δchars | S/D | ms/page P/O/R | Chunks O/R |
| --- | ---: | --- | ---: | --- | --- | --- |
| dsa/balanced-trees.pdf | 103 | 26.49 / 24.31 / 25.37 | 39 | 0/0 | 1.582 / 4.931 / 4.270 | 55/55 |
| dsa/graph.pdf | 83 | 7.05 / 7.05 / 6.02 | 0 | 0/19 | 1.654 / 3.848 / 2.422 | 42/42 |
| dsa/heap.pdf | 48 | 2.38 / 0.73 / 1.95 | 0 | 0/0 | 1.806 / 5.784 / 4.945 | 26/26 |
| dsa/lists-part1.pdf | 73 | 16.22 / 15.26 / 15.90 | 470 | 0/0 | 1.771 / 5.837 / 4.935 | 40/40 |
| dsa/lists-part2_.pdf | 107 | 5.75 / 2.97 / 2.94 | 210 | 0/0 | 1.913 / 6.608 / 6.225 | 61/61 |
| dsa/quicksort.pdf | 54 | 38.33 / 38.33 / 37.53 | 0 | 0/1 | 1.485 / 3.809 / 2.222 | 27/27 |
| dsa/recursion.pdf | 102 | 6.79 / 5.53 / 5.63 | 445 | 0/0 | 2.240 / 7.879 / 7.500 | 69/68 |
| dsa/search-hash.pdf | 68 | 2.14 / 1.31 / 1.76 | 0 | 0/0 | 1.728 / 5.808 / 5.907 | 36/36 |
| dsa/self-adjusting.pdf | 35 | 10.03 / 9.68 / 9.57 | 1011 | 0/0 | 2.905 / 16.060 / 11.962 | 95/98 |
| dsa/sorting.pdf | 60 | 6.39 / 3.83 / 5.53 | 0 | 0/0 | 1.614 / 4.857 / 4.292 | 32/32 |
| dsa/splay-tree.pdf | 15 | 31.37 / 31.37 / 29.29 | 0 | 0/0 | 1.314 / 3.388 / 1.694 | 8/8 |
| dsa/tree-concept.pdf | 63 | 17.19 / 10.23 / 11.99 | 0 | 0/0 | 1.582 / 5.034 / 4.267 | 32/32 |
| operating-system/Chapter 1_Introduction to OS.pdf | 74 | 27.47 / 27.18 / 12.44 | 151 | 0/8 | 1.845 / 4.911 / 3.237 | 40/40 |
| operating-system/Chapter 2_Operating System Structures.pdf | 38 | 9.64 / 9.64 / 9.64 | 0 | 0/159 | 1.617 / 4.254 / 2.951 | 20/20 |
| operating-system/Chapter 3_Processes.pdf | 61 | 12.05 / 12.05 / 10.22 | 0 | 0/139 | 1.707 / 4.428 / 3.047 | 32/32 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 47 | 10.11 / 10.11 / 9.53 | 0 | 0/124 | 1.949 / 4.927 / 3.233 | 25/25 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 62 | 26.78 / 19.83 / 20.35 | 49 | 20/272 | 2.047 / 5.435 / 4.001 | 34/34 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 62 | 17.26 / 16.50 / 16.14 | 147 | 0/204 | 1.981 / 5.239 / 3.941 | 33/33 |
| operating-system/Chapter 7_Synchronization Examples.pdf | 32 | 18.60 / 18.60 / 17.24 | 228 | 0/94 | 1.862 / 4.770 / 3.052 | 18/18 |
| operating-system/Chapter 8_Main Memory.pdf | 87 | 13.41 / 13.42 / 13.25 | 25 | 1/37 | 1.907 / 5.286 / 3.261 | 45/45 |
| operating-system/Chapter 9_Virtual Memory.pdf | 75 | 21.75 / 19.13 / 18.49 | 13 | 40/352 | 1.804 / 4.870 / 3.555 | 41/41 |
| operating-system/Chapter10A_File-System Interface.pdf | 29 | 15.72 / 13.27 / 13.27 | 0 | 5/127 | 2.821 / 7.490 / 4.328 | 15/15 |
| operating-system/Chapter10B_File-System Implementation.pdf | 36 | 21.35 / 21.20 / 21.20 | 50 | 2/138 | 1.855 / 5.071 / 3.584 | 19/19 |
| program-fundamentals/02-Basic.pdf | 35 | 17.72 / 17.47 / 16.21 | 0 | 0/0 | 1.612 / 3.923 / 3.432 | 18/18 |
| program-fundamentals/03-Operations.pdf | 40 | 19.74 / 19.74 / 19.74 | 0 | 0/1 | 1.582 / 3.766 / 3.148 | 20/20 |
| program-fundamentals/04-Libraries.pdf | 18 | 11.41 / 11.49 / 11.41 | 6 | 0/7 | 1.426 / 3.539 / 2.279 | 9/9 |
| program-fundamentals/05-Control_Flow_If_else.pdf | 38 | 16.75 / 16.54 / 16.75 | 1 | 0/0 | 1.146 / 2.798 / 2.356 | 19/19 |
| program-fundamentals/06-Control_Flow_Loop.pdf | 30 | 21.54 / 21.54 / 21.54 | 0 | 0/0 | 1.379 / 3.145 / 2.844 | 15/15 |
| program-fundamentals/07-Array_Structure_Pointer.pdf | 42 | 14.44 / 14.53 / 14.44 | 14 | 0/13 | 1.741 / 4.311 / 3.845 | 21/21 |
| program-fundamentals/08-Function.pdf | 40 | 16.00 / 16.00 / 16.00 | 0 | 0/10 | 1.509 / 3.646 / 2.989 | 20/20 |
| program-fundamentals/09-Recursive.pdf | 16 | 13.64 / 13.64 / 13.64 | 0 | 0/4 | 1.615 / 3.647 / 3.403 | 8/8 |
| program-fundamentals/10-Class.pdf | 36 | 16.33 / 15.66 / 16.33 | 0 | 0/0 | 1.576 / 3.764 / 3.545 | 18/18 |
| program-fundamentals/11-Operator overloading and Inheritance.pdf | 27 | 25.91 / 25.91 / 25.91 | 116 | 0/0 | 1.635 / 4.386 / 3.127 | 14/14 |
| program-fundamentals/12-Template.pdf | 12 | 6.93 / 6.93 / 6.93 | 0 | 0/2 | 1.403 / 3.661 / 3.524 | 6/6 |
| program-fundamentals/13-Polymorphism.pdf | 12 | 11.58 / 11.58 / 11.58 | 0 | 0/0 | 1.652 / 3.716 / 2.510 | 6/6 |
| program-fundamentals/14-Vector_Iterator.pdf | 11 | 7.79 / 7.79 / 7.79 | 0 | 0/0 | 1.642 / 4.019 / 3.856 | 6/6 |
| web-programming/AJAX.pdf | 15 | 16.67 / 16.67 / 16.67 | 0 | 0/15 | 1.646 / 3.428 / 2.249 | 8/8 |
| web-programming/CSS.pdf | 56 | 18.13 / 18.13 / 17.16 | 196 | 0/92 | 1.498 / 3.445 / 2.143 | 27/28 |
| web-programming/HTML.pdf | 45 | 13.92 / 13.92 / 13.92 | 42 | 0/101 | 1.644 / 4.039 / 2.539 | 23/23 |
| web-programming/Introduction to Web Programming.pdf | 16 | 11.68 / 11.68 / 9.57 | 0 | 0/36 | 1.715 / 4.098 / 2.549 | 8/8 |
| web-programming/JavaScript.pdf | 54 | 12.10 / 12.10 / 11.20 | 0 | 0/93 | 1.698 / 4.115 / 2.727 | 27/27 |
| web-programming/MVC Model.pdf | 7 | 12.24 / 12.24 / 12.24 | 0 | 0/11 | 1.844 / 3.973 / 2.657 | 4/4 |
| web-programming/PHP Cookies.pdf | 8 | 23.26 / 23.26 / 19.51 | 0 | 0/15 | 1.539 / 3.753 / 2.214 | 4/4 |
| web-programming/PHP Sessions.pdf | 13 | 19.74 / 19.74 / 16.44 | 0 | 0/22 | 1.671 / 3.894 / 2.437 | 7/7 |
| web-programming/PHP.pdf | 59 | 13.17 / 13.17 / 12.80 | 0 | 0/134 | 1.498 / 3.629 / 2.360 | 30/30 |
| web-programming/Responsive Design.pdf | 25 | 17.19 / 17.19 / 13.11 | 0 | 0/52 | 1.641 / 3.873 / 2.416 | 13/13 |
| web-programming/SEO.pdf | 56 | 12.10 / 12.10 / 12.10 | 0 | 0/165 | 1.906 / 4.598 / 3.047 | 28/28 |
| web-programming/Security Issues.pdf | 20 | 12.50 / 12.50 / 12.50 | 0 | 0/52 | 1.784 / 4.725 / 3.080 | 10/10 |

## Per-file code, bullets and indentation

Code is ordered source-line matches P/O/R, with the source count in parentheses. Bullets and explicit indentation are line counts P/O/R. All values are extraction-level counts before heading labels are prepended.

| PDF | Code P/O/R (source) | Bullets P/O/R | Indented P/O/R |
| --- | --- | --- | --- |
| dsa/balanced-trees.pdf | 23 / 23 / 23 (23) | 27 / 27 / 27 | 0 / 0 / 0 |
| dsa/graph.pdf | 0 / 0 / 0 (0) | 66 / 66 / 66 | 0 / 0 / 0 |
| dsa/heap.pdf | 25 / 25 / 25 (25) | 24 / 24 / 24 | 0 / 0 / 0 |
| dsa/lists-part1.pdf | 472 / 457 / 472 (472) | 55 / 55 / 55 | 0 / 0 / 0 |
| dsa/lists-part2_.pdf | 84 / 84 / 84 (84) | 50 / 50 / 50 | 0 / 0 / 0 |
| dsa/quicksort.pdf | 0 / 0 / 0 (0) | 60 / 60 / 71 | 0 / 0 / 0 |
| dsa/recursion.pdf | 59 / 57 / 59 (59) | 132 / 132 / 132 | 0 / 0 / 0 |
| dsa/search-hash.pdf | 17 / 15 / 15 (17) | 73 / 73 / 73 | 0 / 0 / 0 |
| dsa/self-adjusting.pdf | 195 / 172 / 197 (197) | 6 / 1 / 2 | 3 / 0 / 3 |
| dsa/sorting.pdf | 1 / 1 / 1 (1) | 25 / 25 / 25 | 0 / 0 / 0 |
| dsa/splay-tree.pdf | 0 / 0 / 0 (0) | 12 / 12 / 12 | 0 / 0 / 0 |
| dsa/tree-concept.pdf | 23 / 23 / 23 (23) | 57 / 57 / 57 | 0 / 0 / 0 |
| operating-system/Chapter 1_Introduction to OS.pdf | 3 / 3 / 3 (3) | 9 / 8 / 18 | 0 / 0 / 0 |
| operating-system/Chapter 2_Operating System Structures.pdf | 0 / 8 / 8 (8) | 0 / 159 / 159 | 0 / 0 / 0 |
| operating-system/Chapter 3_Processes.pdf | 188 / 192 / 192 (192) | 115 / 254 / 268 | 4 / 0 / 4 |
| operating-system/Chapter 4_Threads & Concurency.pdf | 68 / 76 / 76 (76) | 65 / 189 / 192 | 0 / 0 / 0 |
| operating-system/Chapter 5_CPU Scheduling.pdf | 40 / 43 / 44 (44) | 161 / 272 / 272 | 79 / 0 / 48 |
| operating-system/Chapter 6_Synchronization Tools.pdf | 239 / 283 / 285 (285) | 141 / 210 / 211 | 143 / 0 / 143 |
| operating-system/Chapter 7_Synchronization Examples.pdf | 106 / 114 / 114 (114) | 1 / 95 / 94 | 18 / 0 / 18 |
| operating-system/Chapter 8_Main Memory.pdf | 1 / 1 / 1 (1) | 0 / 37 / 39 | 17 / 0 / 16 |
| operating-system/Chapter 9_Virtual Memory.pdf | 7 / 10 / 10 (10) | 5 / 356 / 359 | 0 / 0 / 0 |
| operating-system/Chapter10A_File-System Interface.pdf | 16 / 17 / 17 (17) | 69 / 127 / 127 | 3 / 0 / 1 |
| operating-system/Chapter10B_File-System Implementation.pdf | 0 / 0 / 0 (0) | 63 / 138 / 138 | 9 / 0 / 9 |
| program-fundamentals/02-Basic.pdf | 18 / 18 / 18 (18) | 94 / 94 / 99 | 0 / 0 / 0 |
| program-fundamentals/03-Operations.pdf | 13 / 13 / 13 (13) | 105 / 106 / 106 | 0 / 0 / 0 |
| program-fundamentals/04-Libraries.pdf | 0 / 0 / 0 (0) | 10 / 17 / 17 | 0 / 0 / 0 |
| program-fundamentals/05-Control_Flow_If_else.pdf | 0 / 0 / 0 (0) | 76 / 76 / 76 | 0 / 0 / 0 |
| program-fundamentals/06-Control_Flow_Loop.pdf | 0 / 0 / 0 (0) | 67 / 67 / 67 | 0 / 0 / 0 |
| program-fundamentals/07-Array_Structure_Pointer.pdf | 0 / 0 / 0 (0) | 75 / 88 / 88 | 0 / 0 / 0 |
| program-fundamentals/08-Function.pdf | 0 / 0 / 0 (0) | 58 / 68 / 68 | 0 / 0 / 0 |
| program-fundamentals/09-Recursive.pdf | 0 / 0 / 0 (0) | 32 / 36 / 36 | 0 / 0 / 0 |
| program-fundamentals/10-Class.pdf | 1 / 1 / 1 (1) | 91 / 91 / 91 | 0 / 0 / 0 |
| program-fundamentals/11-Operator overloading and Inheritance.pdf | 88 / 88 / 88 (88) | 66 / 64 / 64 | 0 / 0 / 0 |
| program-fundamentals/12-Template.pdf | 31 / 31 / 31 (31) | 42 / 44 / 44 | 0 / 0 / 0 |
| program-fundamentals/13-Polymorphism.pdf | 32 / 32 / 32 (32) | 47 / 47 / 47 | 0 / 0 / 0 |
| program-fundamentals/14-Vector_Iterator.pdf | 0 / 0 / 0 (0) | 24 / 24 / 24 | 0 / 0 / 0 |
| web-programming/AJAX.pdf | 0 / 0 / 0 (0) | 24 / 24 / 24 | 0 / 0 / 0 |
| web-programming/CSS.pdf | 34 / 50 / 50 (50) | 119 / 119 / 119 | 0 / 0 / 0 |
| web-programming/HTML.pdf | 36 / 40 / 40 (40) | 110 / 110 / 110 | 2 / 0 / 2 |
| web-programming/Introduction to Web Programming.pdf | 0 / 0 / 0 (0) | 47 / 47 / 47 | 0 / 0 / 0 |
| web-programming/JavaScript.pdf | 183 / 191 / 191 (191) | 133 / 133 / 133 | 1 / 0 / 1 |
| web-programming/MVC Model.pdf | 0 / 0 / 0 (0) | 11 / 11 / 11 | 0 / 0 / 0 |
| web-programming/PHP Cookies.pdf | 0 / 0 / 0 (0) | 15 / 15 / 15 | 0 / 0 / 0 |
| web-programming/PHP Sessions.pdf | 0 / 0 / 0 (0) | 24 / 24 / 24 | 0 / 0 / 0 |
| web-programming/PHP.pdf | 4 / 8 / 8 (8) | 194 / 194 / 194 | 7 / 0 / 7 |
| web-programming/Responsive Design.pdf | 0 / 0 / 0 (0) | 69 / 69 / 69 | 0 / 0 / 0 |
| web-programming/SEO.pdf | 9 / 25 / 25 (25) | 184 / 184 / 184 | 0 / 0 / 0 |
| web-programming/Security Issues.pdf | 0 / 0 / 0 (0) | 65 / 65 / 65 | 0 / 0 / 0 |

## Keep/remove decisions

- Keep Symbol and dingbat decoding: 68 Symbol spans and 2,499 dingbat spans change
  across the corpus. This preserves operators and slide bullets that plain extraction
  returns in font-specific/private encodings.
- Remove VNI decoding: zero mapping hits and zero changed pages when disabled.
  The 37 characters in VNI-Times/VNI-Helve fonts already contain Unicode text. They pass
  through unchanged; this corpus does not exercise legacy VNI byte decoding.
- Remove radical reconstruction: disabling it changes zero pages.
- Remove large-bracket decoding/merging: zero encoded bracket mapping hits. Its
  ablation changes two recursion table pages because the old merger also consumes
  synthesized vertical bars, not because a genuine large bracket needs repair.
- Remove vertical-bar/absolute-value geometry: only recursion pages 8 and 88 change
  (five non-whitespace characters). Visual inspection confirms the bars come from
  table borders, not absolute-value expressions.
- Remove limit/big-operator attachment: only self-adjusting page 28 changes (26
  characters); the old rule treats `min` as a limit operator and attaches a nearby
  cost expression incorrectly. No calculus-style limit repair is exercised.
- Keep region joining and ordinary superscript/subscript repairs: bypassing region
  rebuilding changes 243 pages and adds 580 short-line fragments, including fragmented
  DSA pseudocode and algorithm indices. Ordinary text blocks now retain their authored
  order instead of being grouped just because bounding boxes touch.
- Keep only horizontal fraction-bar geometry, restricted to pages with math-like
  fonts: removing all strokes changes seven pages. Search-hash page 13's interpolation
  formula and Virtual Memory page 46's proportional allocation need it. Three
  fundamentals pages change whitespace only; recursion pages 8/88 contain table-border
  artifacts. Reject prose table rows as fractions and retain the two useful repairs.
- Preserve explicit code indentation and detached bullets; join adjacent runs on the
  same baseline. Shadow deduplication now requires horizontal overlap, so distinct
  cells with the same `F` are retained. An overlapping footer no longer pulls a code
  block's closing brace ahead of its body.

The layout module shrinks from 719 to 540 lines. The remaining formula machinery
has measured uses in these subjects; removing all of it would lose slide content.
The combined removed-repair ablation changes three pages / 31 characters. Detailed
per-feature effects are in [pdf-reader-ablations.json](pdf-reader-ablations.json).

## Reading order, tables and code checks

| Source slide | Observation |
| --- | --- |
| DSA recursion, page 88 | Visually checked the four-column efficiency table. The old reader rewrites header/data rows as fractions and inserts border `|` characters; the revised reader keeps prose rows and no synthesized bars. |
| DSA recursion, page 8 | Table borders cease becoming absolute-value characters. |
| DSA search-hash, page 13 | Sidebar remains before the body in authored PDF order; the interpolation expression retains its numerator/denominator structure. |
| Operating Systems CPU Scheduling, page 33 | Two-column includes/body code preserves its source order and indentation; the brace near the footer stays after the body. |
| Synthetic two-column/table regressions | Wide text columns keep authored order; distinct identical table cells survive; an overlapping footer cannot reorder ordinary code. |

This is targeted visual/source-order checking, not a ground-truth reading-order
annotation of every slide. Broadly sorting all glyphs by vertical position would
interleave the columns, so the reader retains authored order outside repaired regions.

## OCR warnings and live evidence

Each failed or empty OCR result emits a log warning with the one-based page and
keeps a structured `ocr_failed` warning on the document. A short existing text layer
is retained if OCR fails. A warning-only unit carries its original page but contributes
no text, heading-frequency denominator or chunk locator. Warnings persist in
`documents.extraction_warnings` after completed job payloads are cleared and are
returned by the document list API and rendered next to the document in the course.
An entirely unreadable document persists its warnings before failing with a clear
message. Migration `0022_extraction_warnings` adds the JSONB column with `[]` for
existing documents.

An isolated HTTP smoke used separate PostgreSQL and local model services. A
three-page upload with valid text on pages 1/3 and an OCR-failing page 2 completed
as `embedded`; a fresh `GET /documents` retained page 2's warning. An unreadable
two-page upload failed with both warnings and the readable-content error. The
failure environment lacked Tesseract; corpus measurement used Tesseract with
both languages installed. These are historical observations, not a current smoke run.

| Test scenario | Result / evidence |
| --- | --- |
| OCR exception and whitespace-only OCR | Automated tests verify page, logging, persistence and valid chunks. |
| Short text layer with failed OCR | Existing short text is retained and warning attached. |
| Refresh after completed extraction | Backend integration test checks cleared job payload; frontend remount test checks visible warning. |
| Entirely unreadable upload | Private HTTP job fails with warnings for pages 1/2; no silent ready/empty document. |

## Retrieval before/after

The replay completed over 1,214 original and 1,217 revised chunks, using all four subjects. Both runs use Qwen3 Embedding 0.6B and BAAI bge-reranker-base with the same pinned model SHAs, 768 dimensions, top 10 results and 30 hybrid/rerank candidates. Extraction-measurement and retrieval-index chunk counts agree for each subject. Full metrics and every query rank are saved in [before](pdf-reader-retrieval-before.json) and [after](pdf-reader-retrieval-after.json).

Each table cell is original → revised. A dash in query ranks means no labelled source chunk in the top 10. These 24 hand-labelled probes do not establish statistical significance or broad retrieval accuracy.

| Mode / subject | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
| --- | --- | --- | --- | --- | --- | --- |
| dense / all | 0.833 → 0.833 | 0.875 → 0.875 | 0.917 → 0.917 | 1.000 → 1.000 | 0.874 → 0.874 | 0.904 → 0.904 |
| dense / dsa | 0.667 → 0.667 | 0.667 → 0.667 | 0.833 → 0.833 | 1.000 → 1.000 | 0.724 → 0.724 | 0.787 → 0.787 |
| dense / operating-system | 0.667 → 0.667 | 0.833 → 0.833 | 0.833 → 0.833 | 1.000 → 1.000 | 0.774 → 0.774 | 0.827 → 0.827 |
| dense / program-fundamentals | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |
| dense / web-programming | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |
| hybrid / all | 0.708 → 0.625 | 0.875 → 0.833 | 0.958 → 0.958 | 0.958 → 0.958 | 0.810 → 0.758 | 0.848 → 0.808 |
| hybrid / dsa | 0.333 → 0.167 | 0.667 → 0.500 | 0.833 → 0.833 | 0.833 → 0.833 | 0.533 → 0.408 | 0.608 → 0.513 |
| hybrid / operating-system | 0.833 → 0.667 | 0.833 → 0.833 | 1.000 → 1.000 | 1.000 → 1.000 | 0.875 → 0.792 | 0.905 → 0.844 |
| hybrid / program-fundamentals | 0.667 → 0.667 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 0.833 → 0.833 | 0.877 → 0.877 |
| hybrid / web-programming | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |
| hybrid+rerank / all | 0.750 → 0.750 | 0.917 → 0.917 | 1.000 → 1.000 | 1.000 → 1.000 | 0.840 → 0.840 | 0.880 → 0.880 |
| hybrid+rerank / dsa | 0.333 → 0.333 | 0.667 → 0.667 | 1.000 → 1.000 | 1.000 → 1.000 | 0.556 → 0.556 | 0.665 → 0.665 |
| hybrid+rerank / operating-system | 0.667 → 0.667 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 0.806 → 0.806 | 0.855 → 0.855 |
| hybrid+rerank / program-fundamentals | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |
| hybrid+rerank / web-programming | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 |

| Query (order in fixed query JSON) / source | Dense O/R | Hybrid O/R | Reranked O/R | Gold chunks O/R |
| --- | --- | --- | --- | --- |
| Q1: dsa/balanced-trees.pdf:11 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q2: dsa/balanced-trees.pdf:49 | 7/7 | 5/5 | 3/3 | 1/1 |
| Q3: dsa/graph.pdf:32 | 5/5 | 2/2 | 1/1 | 1/1 |
| Q4: dsa/search-hash.pdf:13 | 1/1 | 2/2 | 2/2 | 1/1 |
| Q5: dsa/recursion.pdf:88 | 1/1 | 1/4 | 4/4 | 1/1 |
| Q6: dsa/lists-part2_.pdf:5 | 1/1 | —/— | 4/4 | 1/1 |
| Q7: operating-system/Chapter 5_CPU Scheduling.pdf:8 | 7/7 | 4/4 | 1/1 | 1/1 |
| Q8: operating-system/Chapter 5_CPU Scheduling.pdf:20 | 2/2 | 1/1 | 1/1 | 1/1 |
| Q9: operating-system/Chapter 5_CPU Scheduling.pdf:21 | 1/1 | 1/2 | 2/2 | 1/1 |
| Q10: operating-system/Chapter 8_Main Memory.pdf:29 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q11: operating-system/Chapter 9_Virtual Memory.pdf:13 | 1/1 | 1/1 | 3/3 | 1/1 |
| Q12: operating-system/Chapter 9_Virtual Memory.pdf:46 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q13: program-fundamentals/02-Basic.pdf:23 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q14: program-fundamentals/05-Control_Flow_If_else.pdf:22 | 1/1 | 2/2 | 1/1 | 1/1 |
| Q15: program-fundamentals/05-Control_Flow_If_else.pdf:25 | 1/1 | 2/2 | 1/1 | 1/1 |
| Q16: program-fundamentals/06-Control_Flow_Loop.pdf:19 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q17: program-fundamentals/07-Array_Structure_Pointer.pdf:25 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q18: program-fundamentals/08-Function.pdf:20 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q19: web-programming/CSS.pdf:6 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q20: web-programming/CSS.pdf:8 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q21: web-programming/JavaScript.pdf:13 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q22: web-programming/PHP Cookies.pdf:3 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q23: web-programming/PHP Cookies.pdf:6 | 1/1 | 1/1 | 1/1 | 1/1 |
| Q24: web-programming/PHP Sessions.pdf:6 | 1/1 | 1/1 | 1/1 | 1/1 |

Ranking changes on the fixed probes:

- dense: 0 improved, 0 worsened, 24 unchanged.
- hybrid: 0 improved, 2 worsened, 22 unchanged.
- hybrid+rerank: 0 improved, 0 worsened, 24 unchanged.

Dense and production hybrid+rerank ranks are identical across all 24 probes.
Unreranked hybrid worsens on the DSA efficiency-table and Operating Systems
round-robin probes: its
aggregate Recall@1 drops from 0.708 to 0.625 and MRR from 0.810 to 0.758.
This regression is retained in the evidence; parser repairs are not a claim of
uniformly improved lexical ranking. The production reranker still returns the
same labelled ranks, with Recall@5 of 1.000 on this small diagnostic set.

This comparison checks that the parser changes still feed the existing retrieval flow with usable source locators. It is not evidence for changing an embedding or reranker model.

## Automated validation

| Check | Result |
| --- | --- |
| Full backend suite, final implementation | 1,168 passed; one existing live-LLM test deselected. |
| Focused PDF/OCR/pipeline regressions | 44 passed. |
| Full frontend suite | 238 passed across 20 test files. |
| Frontend production build | Passed. |
| Ruff (backend and measurement scripts), diff whitespace | Passed. |

The new cases were reproduced failing before their fixes: OCR exception/empty
result metadata, persistent API/UI warning, explicit indentation, adjacent text
runs, identical table cells, overlapping footer/code order, and warning-only pages
changing heading frequency. The existing study-migration round-trip test now targets
its own `0019_blind_ratings` revision rather than assuming the repository head is
always that revision. 

The supplied self-adjusting PDF includes scanned paper pages with a damaged text
layer and complex expressions. The measurements include it as provided. Neither
reader reliably recovers those complex expressions; the removed limit rule's only
effect is a wrong cross-row attachment. Document or textbook removal is outside
this change.

## Reproduce

Use the repository's existing backend dependencies, a read-only four-subject PDF
copy, and Tesseract with `eng+vie`. Set `PDFS` to that copy and `OUTPUT` to a
repository-local output directory. The baseline must be available in local git history.

```sh
PDFS="$PWD/docs/documents"
OUTPUT="$PWD/.pdf-reader-evidence"
BASELINE=760483b9825d646d785e78ef3c56989998142088
mkdir -p "$OUTPUT"
backend/.venv/bin/python docs/measurements/measure_pdf_reader.py \
  --documents "$PDFS" --baseline-ref "$BASELINE" --output "$OUTPUT/current"
backend/.venv/bin/python docs/measurements/ablate_pdf_repairs.py \
  --documents "$PDFS" --baseline-ref "$BASELINE" --output "$OUTPUT/ablations.json"
```

For retrieval, provision disposable pgvector databases for before/after and migrate
each with Alembic from `backend`. Supply `BENCHMARK_DATABASE_URL`, `LLM_GATEWAY_URL`,
`LLM_GATEWAY_KEY`, and `RERANKER_URL` from that environment. Keep Qwen3 Embedding
0.6B (768 dimensions, revision `c25a394dd583836952667c12f008335071b3f43d`) and
BAAI bge-reranker-base (revision `2cfc18c9415c912f9d8155881c133215df768a70`),
using the service versions in [pdf-reader-services.json](pdf-reader-services.json).
Run `replay_pdf_retrieval.py` with `--documents`, `--queries`, `--cache`, and `--output`
pointing to the corpus, `docs/measurements/pdf-reader-queries.json`, a local cache,
and a local result file. Repeat against the other database with
`--reader-ref "$BASELINE"` and a distinct output file. Use fresh databases and
stop disposable services afterwards.

The replay calls the existing `backend/scripts/benchmark_rerank.py` ingestion,
ranking and metric functions. It expands the old capped subject sample to all
48 files and uses 24 fixed file/page-labelled queries (six per subject).
Historical query labels contain UUIDs for an old database and cannot label newly
chunked content. These hand-labelled probes provide a before/after diagnostic,
not a statistical quality evaluation. A file/page's relevant chunks may differ
in count after extraction repairs. The embedding service is called directly using
its existing OpenAI-compatible protocol; this bypasses proxy routing only for this
measurement and does not change weights, pooling, vector dimension (768) or search
parameters. Real model vectors are cached by content in the configured output directory;
use a fresh cache if changing models. Actual `/info` model identifiers, SHAs, pooling and service versions are captured
in [pdf-reader-services.json](pdf-reader-services.json). No fake embeddings or reranker scores are
used in the retrieval evidence (automated tests separately use their existing
stable offline fixtures).

## Rebase validation (2026-10-17)

Rebased onto current main after the four-subject/textbook removal landed. The
warning migration now follows `0021_remove_textbook_scope` as
`0022_extraction_warnings`, preserving one Alembic head. Removed this branch's
obsolete study-test revision pins so the existing test exercises the current schema.

Re-ran extraction with Tesseract in an isolated container. All 48 hashes and all 2,145 pages match;
every non-timing per-file metric, code-order count, warning page, chunk count,
and locator comparison is identical to the earlier final run. The fresh per-file
results are in [pdf-reader-rebased-files.json](pdf-reader-rebased-files.json).
Paired mean extraction times are plain 1.163, original 3.221, revised 2.420 ms/page
(24.9% less than original); timing varies with host load.

Fresh targeted checks: 37 PDF/format unit tests, 12 reader/OCR/pipeline/migration
integration tests, and 48 document-page frontend tests passed. Alembic upgraded a
fresh private database through the new single head; Ruff and whitespace checks
passed. 

Also reran retrieval against fresh isolated before/after databases. Both output JSON objects are
identical to the committed before/after evidence: all 24 probe ranks, metrics,
model identifiers and subject chunk counts reproduce exactly. Dense and
production reranked results remain unchanged; the documented two unreranked
hybrid regressions remain visible.
