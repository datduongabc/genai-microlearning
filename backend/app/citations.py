"""Readable names for where a passage comes from, built from a chunk's saved locator.

The labels are for display only: the saved locator stays exact for source resolution. Shared by
the module, flashcard and video APIs, which name one passage.
"""

import json

# (locator key, Vietnamese word, English singular, English plural, offset to a 1-based number)
_NUMBERED = (
    ("page", "trang", "page", "pages", 0),
    ("slide_index", "slide", "slide", "slides", 0),
    ("table_index", "bảng", "table", "tables", 1),
    ("row_index", "hàng", "row", "rows", 1),
    ("paragraph_index", "đoạn", "paragraph", "paragraphs", 1),
)


def locator_spans(locator: str) -> list[dict] | None:
    """The spans a locator names, or None when it is not a JSON locator. A locator is one span, or
    {"spans": [...]} for a chunk that runs across several."""
    try:
        parsed = json.loads(locator)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return []
    spans = parsed.get("spans")
    spans = spans if isinstance(spans, list) else [parsed]
    return [span for span in spans if isinstance(span, dict)]


def _ranges(numbers: set[int]) -> str:
    ranges: list[list[int]] = []
    for number in sorted(numbers):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges)


def location_parts(spans: list[dict], lang: str = "vi") -> list[str]:
    """"trang 9–10", "slide 3", "01:05–02:05": where the spans are, consecutive numbers merged."""
    parts = []
    for key, vi, en_one, en_many, offset in _NUMBERED:
        numbers = {span[key] + offset for span in spans if isinstance(span.get(key), int)}
        if numbers:
            word = vi if lang == "vi" else en_one if len(numbers) == 1 else en_many
            parts.append(f"{word} {_ranges(numbers)}")
    times = [span[key] for span in spans for key in ("start_ms", "end_ms") if isinstance(span.get(key), int)]
    if times:
        parts.append("–".join(f"{t // 60000:02d}:{t // 1000 % 60:02d}" for t in (min(times), max(times))))
    return parts


def citation_label(filename: str, locator: str) -> str:
    """One passage, as the module and flashcard pages list it: "x.pdf · trang 9 · FCFS"."""
    spans = locator_spans(locator)
    if spans is None:
        return filename if locator.lstrip().startswith(("{", "[")) else locator
    heading = next(
        (span[key] for span in spans for key in ("heading", "slide_title") if isinstance(span.get(key), str) and span[key]),
        None,
    )
    return " · ".join(part for part in (filename, *location_parts(spans), heading) if part)
