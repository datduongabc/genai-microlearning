"""Writes the small lecture documents that test_ingestion_snapshot.py reads.

The real course files cannot be committed, so these imitate what broke on them: a handout's page
counter ("2 / 3"), numbered list items, code and data lines that start with a number, formulas, real
numbered section headings, a title repeated on every slide, tables and Vietnamese text. Run it only to
change the fixtures, then regenerate the snapshot (see the test). The PDF embeds DejaVu Sans, so
reading it needs no installed font.

    uv run python tests/unit/fixtures/ingestion/make_fixtures.py
"""

from datetime import UTC, datetime
from pathlib import Path

import docx
import pymupdf
from pptx import Presentation
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

PDF_PAGES = [
    [
        (20, "Chương 1. Giới hạn dãy số"),
        (11, "1 Khái niệm dãy số"),
        (11, "Một dãy số là một hàm số xác định trên tập các số tự nhiên, nhận giá trị thực."),
        (11, "Ta thường ký hiệu dãy số là (x_n) và gọi x_n là số hạng tổng quát của dãy."),
        (11, "1 Nếu α > 1 thì dãy a_n = n^α tiến ra vô cùng khi n tăng."),
        (11, "2 Nếu 0 < α < 1 thì dãy vẫn tăng nhưng chậm hơn tuyến tính."),
        (11, "1.1 Dãy bị chặn"),
        (11, "Dãy (x_n) bị chặn trên nếu tồn tại số M sao cho x_n ≤ M với mọi n."),
        (9, "1 / 3"),
    ],
    [
        (11, "2 Giới hạn của dãy số"),
        (11, "Dãy (x_n) có giới hạn L nếu với mọi ε > 0 tồn tại N sao cho |x_n − L| < ε khi n > N."),
        (11, "Ví dụ: lim (1 + 1/n)^n = e, một giới hạn quan trọng trong giải tích."),
        (11, "100 INT_LIT"),
        (11, "0 => { 0 }"),
        (11, "65000 words of lecture notes were transcribed for this chapter."),
        (9, "2 / 3"),
    ],
    [
        (11, "3 Lexical rules"),
        (11, "A scanner groups characters into tokens by the longest match rule."),
        (11, "Each token class is described by a regular expression over the input alphabet."),
        (11, "2.3 Regular expressions"),
        (11, "The union, concatenation and Kleene star build every regular language."),
        (9, "3 / 3"),
    ],
]


def write_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for lines in PDF_PAGES:
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname="dejavu", fontfile=FONT)
        y = 60
        for size, text in lines:
            page.insert_text((50, y), text, fontsize=size, fontname="dejavu")
            y += size * 2.2
    # A fixed creation date and id keep the bytes identical between runs.
    doc.set_metadata({"creationDate": "D:20261112000000", "modDate": "D:20261112000000", "producer": "fixture"})
    doc.subset_fonts()  # embed only the glyphs used: the full font is 400 KB
    doc.save(path, garbage=4, deflate=True, no_new_id=True)
    doc.close()


def write_pptx(path: Path) -> None:
    deck = Presentation()
    slides = [
        ("Encapsulation", ["A class hides its fields behind methods.", "Getters and setters control access."]),
        ("Access modifiers", ["private: only the class itself", "protected: the class and its subclasses",
                              "public: every caller"]),
        ("Lợi ích của đóng gói", ["Giảm phụ thuộc giữa các lớp.", "Dễ thay đổi cài đặt bên trong."]),
    ]
    for title, bullets in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = bullets[0]
        for bullet in bullets[1:]:
            body.add_paragraph().text = bullet
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "Comparison"
    rows = [("Modifier", "Same class", "Subclass", "Other"), ("private", "yes", "no", "no"),
            ("protected", "yes", "yes", "no"), ("public", "yes", "yes", "yes")]
    table = slide.shapes.add_table(len(rows), 4, Inches(0.5), Inches(1.5), Inches(9), Inches(2)).table
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
            table.cell(r, c).text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    deck.core_properties.created = deck.core_properties.modified = datetime(2026, 11, 12, tzinfo=UTC)
    deck.save(path)


def write_docx(path: Path) -> None:
    document = docx.Document()
    document.add_heading("Process scheduling", level=1)
    document.add_paragraph("The scheduler picks the next process from the ready queue.")
    document.add_heading("First-come, first-served", level=2)
    document.add_paragraph("Processes run in arrival order, so a long job delays every job behind it.")
    document.add_heading("Round robin", level=2)
    document.add_paragraph("Each process runs for one time quantum and then goes to the back of the queue.")
    table = document.add_table(rows=3, cols=2)
    for r, (name, wait) in enumerate([("Algorithm", "Average wait"), ("FCFS", "17 ms"), ("RR", "5.66 ms")]):
        table.cell(r, 0).text, table.cell(r, 1).text = name, wait
    document.save(path)


if __name__ == "__main__":
    write_pdf(HERE / "lecture.pdf")
    write_pptx(HERE / "slides.pptx")
    write_docx(HERE / "notes.docx")
    print("wrote", ", ".join(p.name for p in sorted(HERE.glob("*.*")) if p.suffix != ".py"))
