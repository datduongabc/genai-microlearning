"""What the readers and the chunker make of three small lecture documents, compared with a snapshot.

A wrong heading rule once cut hundreds of extra chunks on real PDFs and labelled them with page
counters, and every unit test passed because each looked at one line. This test looks at the whole
output: chunk boundaries, labels and text. A change in reading or cutting fails it until someone
regenerates the snapshot on purpose and reviews the diff:

    UPDATE_INGESTION_SNAPSHOT=1 uv run pytest tests/unit/test_ingestion_snapshot.py

The formula re-reader is switched off, so no model is called and the result does not depend on one.
"""

import json
import os
from pathlib import Path

import pytest

from app.config import settings
from app.ingestion.chunker import chunk_units, document_title, locator_heading
from app.ingestion.document_readers import read_document

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"
SNAPSHOT = FIXTURES / "snapshot.json"
DOCUMENTS = ("lecture.pdf", "slides.pptx", "notes.docx")


def _span_headings(locator: dict) -> list[str | None]:
    """The heading of every unit a chunk holds; a merged chunk is labelled only by the first one."""
    return [span.get("heading") for span in locator.get("spans", [locator])]


def _read_and_cut(name: str) -> list[dict]:
    units = read_document(str(FIXTURES / name), course_id="snapshot")
    return [
        {"heading": locator_heading(c.locator), "unit_headings": _span_headings(c.locator), "strategy": c.strategy,
         "tokens": c.token_count, "content": c.content}
        for c in chunk_units(units, title=document_title(name))
    ]


@pytest.fixture
def no_formula_model(monkeypatch):
    monkeypatch.setattr(settings, "formula_vision_model", "")


def test_ingestion_matches_snapshot(no_formula_model):
    actual = {name: _read_and_cut(name) for name in DOCUMENTS}
    if os.environ.get("UPDATE_INGESTION_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name in DOCUMENTS:
        assert actual[name] == expected[name], (
            f"{name} is read or cut differently from tests/unit/fixtures/ingestion/snapshot.json. If the change "
            "is intended, rerun with UPDATE_INGESTION_SNAPSHOT=1 and review the snapshot diff."
        )


def test_page_counters_and_list_items_are_not_labels(no_formula_model):
    """The two mistakes of the old heading rule, stated directly so the snapshot cannot absorb them."""
    labels = {h for c in _read_and_cut("lecture.pdf") for h in c["unit_headings"]}
    assert not labels & {"1 / 3", "2 / 3", "3 / 3", "100 INT_LIT", "0 => { 0 }"}
    assert not any(label and label.startswith(("1 Nếu", "2 Nếu", "65000")) for label in labels)
    assert {"1 Khái niệm dãy số", "2 Giới hạn của dãy số", "3 Lexical rules"} <= labels
