#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export LC_ALL=C.UTF-8
for source in diagrams/*.tex; do
  latexmk -pdf -interaction=nonstopmode -halt-on-error -file-line-error -outdir=diagrams "$source" > "${source%.tex}.build.log" 2>&1
done
latexmk -pdf -interaction=nonstopmode -halt-on-error -file-line-error report.tex > build.log 2>&1
if rg -n '(^!|[Ee]rror:|Fatal error|Emergency stop|Infinite glue shrinkage|Undefined control sequence|undefined references|Reference .* undefined|Citation .* undefined|There were undefined|multiply defined)' report.log report.blg diagrams/*.log; then
  echo 'Build failed validation: unresolved LaTeX diagnostics.' >&2
  exit 1
fi
printf 'Build passed: report.pdf and all diagram PDFs; no errors or undefined references.\n'
