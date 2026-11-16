"""Voice and image models, called through the LLM gateway.

LiteLLM 1.100.1 has no OpenRouter route for speech, and its image route cannot reach the
chat-completions form Nano Banana needs, so devops/gateway/config.yaml forwards
`/openrouter/*` to OpenRouter as an authenticated pass-through (the gateway keeps the
provider key). A pass-through cannot add a provider pin by itself, so the alias table,
the upstream tag and `data_collection: deny` live here and are sent in every request.
"""

import base64
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.config import settings

DEFAULT_VOICE = "voice-heygen"
DEFAULT_IMAGE_MODEL = "image-nano-banana"
REQUEST_TIMEOUT_SECONDS = 240.0
# Upstream is pinned, never served by another, and never allowed to keep or train on course material.
_PROVIDER_PIN = {"allow_fallbacks": False, "data_collection": "deny"}


# Before the second try of a call that raised MediaError. The render voices 3 scenes at once and
# the pictures job draws 5, so a 429 met at once is met again by a try made at once.
RETRY_WAIT_SECONDS = 5.0
# A provider's Retry-After is honoured up to this: the job holds a worker while it waits, and past
# it the scene fails for the lecturer to try again later rather than the job sitting idle.
MAX_RETRY_WAIT_SECONDS = 30.0
# Patched by the tests, so that none of them waits.
sleep = time.sleep


class MediaError(Exception):
    """A reply with no audio or image, a busy provider (429 or 5xx) or an unreachable gateway:
    transient. The job step that made the call waits (wait_before_retry) and tries it once more;
    if that fails too, the scene fails and the lecturer tries again from the editor. The job itself
    is not retried for it. `retry_after` is the provider's Retry-After in seconds, when it sent one."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def retry_delay(error: MediaError, attempt: int) -> float:
    """How long to wait after the `attempt`-th try (1-based) failed with `error`."""
    if error.retry_after is not None:
        return min(max(error.retry_after, 0.0), MAX_RETRY_WAIT_SECONDS)
    return min(RETRY_WAIT_SECONDS * attempt, MAX_RETRY_WAIT_SECONDS)


def wait_before_retry(error: MediaError, attempt: int) -> None:
    sleep(retry_delay(error, attempt))


def _retry_after(response: httpx.Response) -> float | None:
    """Retry-After as seconds from now: a number of seconds or an HTTP date (RFC 9110)."""
    value = (response.headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    # NaN, infinity and a negative wait are no wait to honour: sleep(nan) raises, so the default backoff applies.
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


@dataclass(frozen=True)
class VoiceModel:
    slug: str
    provider: str
    voice: str
    label: str
    price: str
    # Derived from the per-8-minute-video figures of the spec at about 900 characters a minute
    # (7200 characters), because a speech reply is raw audio with no usage to read a cost from.
    usd_per_1k_chars_estimate: float


@dataclass(frozen=True)
class ImageModel:
    slug: str
    provider: str
    endpoint: str  # "chat" or "images", the two shapes OpenRouter serves image models in
    label: str
    price: str


VOICES: dict[str, VoiceModel] = {
    "voice-heygen": VoiceModel("heygen/heygen-voice-1-turbo", "heygen", "nadine", "HeyGen Voice 1 Turbo", "khoảng $0,03 một video 8 phút", 0.0042),
    "voice-gemini-lite": VoiceModel("google/gemini-3.8-flash-lite-tts", "google-ai-studio", "Kore", "Gemini 3.8 Flash-Lite TTS", "khoảng $0,07 một video 8 phút", 0.0097),
    "voice-gemini-flash": VoiceModel("google/gemini-3.8-flash-tts", "google-ai-studio", "Kore", "Gemini 3.8 Flash TTS", "khoảng $0,11 một video 8 phút", 0.0153),
}
IMAGE_MODELS: dict[str, ImageModel] = {
    "image-nano-banana": ImageModel("google/gemini-nano-banana-2.1", "google-ai-studio", "chat", "Gemini Nano Banana 2.1", "khoảng $0,037 một ảnh"),
    "image-qwen": ImageModel("qwen/qwen-image-2.1-turbo", "alibaba", "images", "Qwen Image 2.1 Turbo", "khoảng $0,018 một ảnh"),
}


@dataclass(frozen=True)
class Speech:
    pcm: bytes  # 16-bit mono little-endian
    sample_rate: int
    served_model: str
    cost: float | None


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime: str
    served_model: str
    cost: float | None


def _provider(tag: str) -> dict:
    return {"order": [tag], **_PROVIDER_PIN}


def _gateway_client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.llm_gateway_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _post(client: httpx.Client | None, path: str, body: dict) -> httpx.Response:
    owned = client is None
    client = client or _gateway_client()
    try:
        response = client.post(f"/openrouter{path}", json=body, headers={"Authorization": f"Bearer {settings.llm_gateway_key}"})
    except httpx.TransportError as exc:
        raise MediaError(f"gateway unreachable: {exc}") from exc
    finally:
        if owned:
            client.close()
    if response.status_code == 429 or response.status_code >= 500:
        raise MediaError(f"provider busy ({response.status_code}): {_error_message(response)}",
                         retry_after=_retry_after(response))
    if response.status_code >= 400:
        # A refusal repeats on every retry, so it stops the job instead.
        raise ValueError(f"HTTP {response.status_code}: {_error_message(response)}")
    return response


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json()["error"]
        return str(error["message"] if isinstance(error, dict) else error)
    except (ValueError, KeyError, TypeError):
        return response.text[:300] or f"HTTP {response.status_code}"


def _sniff_mime(data: bytes) -> str:
    """The images endpoint names no type, and a data URL may leave it out."""
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def synthesize_speech(alias: str, text: str, *, client: httpx.Client | None = None) -> Speech:
    model = VOICES.get(alias)
    if model is None:
        raise ValueError(f"unknown voice model {alias!r}")
    response = _post(client, "/audio/speech", {
        "model": model.slug, "input": text, "voice": model.voice,
        "response_format": "pcm", "provider": _provider(model.provider),
    })
    # Gemini answers at 24 kHz and HeyGen at 44.1 kHz; only the header says which.
    match = re.search(r"rate=(\d+)", response.headers.get("content-type", ""))
    if not response.content or match is None:
        raise MediaError(f"{alias} returned no audio or no sample rate")
    estimated_cost = len(text) / 1000 * model.usd_per_1k_chars_estimate
    return Speech(response.content, int(match.group(1)), model.slug, estimated_cost)


def generate_image(alias: str, prompt: str, aspect_ratio: str = "4:3", *, client: httpx.Client | None = None) -> GeneratedImage:
    model = IMAGE_MODELS.get(alias)
    if model is None:
        raise ValueError(f"unknown image model {alias!r}")
    provider = _provider(model.provider)
    if model.endpoint == "chat":
        response = _post(client, "/chat/completions", {
            "model": model.slug, "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": prompt}],
            "image_config": {"aspect_ratio": aspect_ratio}, "usage": {"include": True}, "provider": provider,
        })
    else:
        response = _post(client, "/images", {
            "model": model.slug, "prompt": prompt, "aspect_ratio": aspect_ratio,
            "usage": {"include": True}, "provider": provider,
        })
    try:
        body = response.json()
        if model.endpoint == "chat":
            url = body["choices"][0]["message"]["images"][0]["image_url"]["url"]
            if not url.startswith("data:"):
                # Not transient: the same request would return the same kind of URL.
                not_data_url = True
            else:
                not_data_url = False
                header, _, encoded = url.partition(",")
                mime = header.removeprefix("data:").split(";")[0]
        else:
            not_data_url, mime, encoded = False, "", body["data"][0]["b64_json"]
        data = base64.b64decode(encoded) if not not_data_url else b""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise MediaError(f"{alias} returned no image") from exc
    if not_data_url:
        raise ValueError(f"{alias} returned an image URL, not a data URL; fetching it is not supported")
    if not data:
        raise MediaError(f"{alias} returned an empty image")
    mime = mime or _sniff_mime(data)
    cost = (body.get("usage") or {}).get("cost")
    return GeneratedImage(data, mime, model.slug, cost)
