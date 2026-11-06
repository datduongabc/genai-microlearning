# Building the report

Install Bash, latexmk, PDFLaTeX, BibTeX and ripgrep. TeX Live needs recommended/extra packages, TikZ/standalone, English/Vietnamese support, lmodern, etoolbox, pdflscape, needspace and seqsplit.

Run `./build.sh` in this directory. It builds the standalone TikZ diagrams, then the report and bibliography, and rejects errors or unresolved references/citations.

To clean intermediate files while keeping PDFs, run `latexmk -c report.tex` and `latexmk -c -outdir=diagrams diagrams/*.tex`. For a full rebuild, use `-C` instead (this also removes PDFs), then run `./build.sh`.

Edit `diagrams/*.tex` directly; optional `python3 make_diagrams.py` checks their document boundaries. `longtable-compat.tex` provides the template's finite-glue compatibility fix. Template licensing is in LICENSE.txt.
