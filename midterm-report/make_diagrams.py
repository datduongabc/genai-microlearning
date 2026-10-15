#!/usr/bin/env python3
"""Validate maintained TikZ sources. Edit diagrams/*.tex directly; build.sh renders PDFs."""
from pathlib import Path
root=Path(__file__).resolve().parent
for source in sorted((root/'diagrams').glob('*.tex')):
    text=source.read_text()
    assert r'\begin{document}' in text and r'\end{document}' in text, source
    print(source.relative_to(root))
