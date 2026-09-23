"""Chooses the text the Planner reads for a section that is longer than it can take."""

SEPARATOR = "\n\n"
# Below this a slice is too short to say anything about its chunk, so it is
# better to look at fewer chunks than at fragments of many.
MIN_SLICE_CHARS = 200


def build_scope_digest(texts: list[str], budget: int) -> str:
    """Passes a short section through whole; otherwise takes evenly spaced
    chunks in order, so the Planner sees the whole section and not only its
    opening (the Planner used to read just the first 8000 characters of a
    document). Deterministic, so the same section always gives the same input.
    """
    texts = [t for t in texts if t.strip()]
    if not texts:
        return ""
    joined = SEPARATOR.join(texts)
    if len(joined) <= budget:
        return joined

    picks = max(1, min(len(texts), budget // MIN_SLICE_CHARS))
    if picks == 1:
        indexes = [0]
    else:
        indexes = sorted({round(i * (len(texts) - 1) / (picks - 1)) for i in range(picks)})
    slice_chars = max(1, (budget - len(SEPARATOR) * (len(indexes) - 1)) // len(indexes))
    return SEPARATOR.join(texts[i][:slice_chars] for i in indexes)
