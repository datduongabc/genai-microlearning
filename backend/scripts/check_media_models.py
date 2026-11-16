"""Call each voice and image alias once through the running gateway and report what served it.

Paid (about $0.07 in all): run it on purpose, not in CI. Exits non-zero if any alias fails, e.g.
when no provider that satisfies data_collection: deny is left for it.

    docker exec do-an-worker /app/.venv/bin/python /app/scripts/check_media_models.py
"""

import sys

from app.llm import media

SENTENCE = "Xin chào, đây là một câu thử giọng."
PICTURE = "A small flat blue circle on a white background, no text."


def main() -> int:
    failures = 0
    for alias in media.VOICES:
        try:
            speech = media.synthesize_speech(alias, SENTENCE)
            print(f"OK   {alias}: {speech.served_model}, {speech.sample_rate} Hz, {len(speech.pcm)} bytes pcm")
        except (media.MediaError, ValueError) as exc:
            failures += 1
            print(f"FAIL {alias}: {exc}")
    for alias in media.IMAGE_MODELS:
        try:
            image = media.generate_image(alias, PICTURE)
            print(f"OK   {alias}: {image.served_model}, {image.mime}, {len(image.data)} bytes, cost {image.cost}")
        except (media.MediaError, ValueError) as exc:
            failures += 1
            print(f"FAIL {alias}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
