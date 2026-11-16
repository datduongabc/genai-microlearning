"""The voice and image client: what it asks the gateway for, and how it reads the reply.
HTTP is replaced by httpx.MockTransport, so no model is called."""

import base64
import json

import httpx
import pytest

from app.llm import media


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gateway.test")


def _pcm_reply(content_type: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x01\x02\x03\x04", headers={"content-type": content_type})

    return handler


@pytest.mark.parametrize(("content_type", "rate"), [
    ("audio/pcm;rate=44100;channels=1", 44100),
    ("audio/pcm; rate=24000; channels=1", 24000),
])
def test_speech_reads_the_sample_rate_from_the_content_type(content_type, rate):
    speech = media.synthesize_speech("voice-heygen", "Xin chào", client=_client(_pcm_reply(content_type)))

    assert speech.sample_rate == rate
    assert speech.pcm == b"\x01\x02\x03\x04"


def test_speech_without_a_rate_in_the_content_type_is_a_media_error():
    with pytest.raises(media.MediaError):
        media.synthesize_speech("voice-heygen", "Xin chào", client=_client(_pcm_reply("audio/pcm")))


def test_speech_request_names_the_alias_and_the_alias_voice():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"\x00\x00", headers={"content-type": "audio/pcm;rate=44100;channels=1"})

    speech = media.synthesize_speech("voice-heygen", "Xin chào", client=_client(handler))

    body = seen["body"]
    assert seen["path"] == "/openrouter/audio/speech"
    assert seen["auth"].startswith("Bearer ")
    assert body["model"] == "heygen/heygen-voice-1-turbo"
    assert body["voice"] == "nadine"
    assert body["response_format"] == "pcm"
    assert body["input"] == "Xin chào"
    assert speech.served_model == "heygen/heygen-voice-1-turbo"


@pytest.mark.parametrize("alias", [*media.VOICES, *media.IMAGE_MODELS])
def test_every_request_pins_the_provider_and_denies_data_collection(alias):
    """The gateway cannot add this for a pass-through, so the client must: course material
    must not reach an endpoint that stores or trains on it, nor be served by another one."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        if "audio" in request.url.path:
            return httpx.Response(200, content=b"\x00\x00", headers={"content-type": "audio/pcm;rate=24000"})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"x").decode()}],
                                         "choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64,eA=="}}]}}]})

    if alias in media.VOICES:
        media.synthesize_speech(alias, "hi", client=_client(handler))
        tag = media.VOICES[alias].provider
    else:
        media.generate_image(alias, "a cat", client=_client(handler))
        tag = media.IMAGE_MODELS[alias].provider

    assert seen["body"]["provider"] == {"order": [tag], "allow_fallbacks": False, "data_collection": "deny"}


def test_the_aliases_are_the_five_the_gateway_offers():
    assert set(media.VOICES) == {"voice-heygen", "voice-gemini-lite", "voice-gemini-flash"}
    assert set(media.IMAGE_MODELS) == {"image-nano-banana", "image-qwen"}
    assert media.DEFAULT_VOICE in media.VOICES
    assert media.DEFAULT_IMAGE_MODEL in media.IMAGE_MODELS
    assert {v.provider for v in media.VOICES.values()} | {m.provider for m in media.IMAGE_MODELS.values()} == {
        "heygen", "google-ai-studio", "alibaba"}


def test_an_unknown_alias_is_a_value_error():
    with pytest.raises(ValueError, match="voice-nope"):
        media.synthesize_speech("voice-nope", "hi", client=_client(_pcm_reply("audio/pcm;rate=24000")))


def test_image_from_chat_completions_is_decoded():
    png = b"\xff\xd8\xff fake jpeg bytes"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        url = "data:image/jpeg;base64," + base64.b64encode(png).decode()
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {"url": url}}]}}],
                                         "usage": {"cost": 0.037}})

    image = media.generate_image("image-nano-banana", "a diagram", client=_client(handler))

    assert image.data == png
    assert image.mime == "image/jpeg"
    assert image.cost == 0.037
    assert seen["path"] == "/openrouter/chat/completions"
    assert seen["body"]["modalities"] == ["image", "text"]
    assert seen["body"]["image_config"] == {"aspect_ratio": "4:3"}
    assert seen["body"]["usage"] == {"include": True}


def test_image_from_images_endpoint_is_decoded():
    png = b"\x89PNG fake"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}], "usage": {"cost": 0.018}})

    image = media.generate_image("image-qwen", "a diagram", aspect_ratio="16:9", client=_client(handler))

    assert image.data == png
    assert image.mime == "image/png"
    assert image.cost == 0.018
    assert seen["path"] == "/openrouter/images"
    assert seen["body"]["aspect_ratio"] == "16:9"
    assert seen["body"]["model"] == "qwen/qwen-image-2.1-turbo"


@pytest.mark.parametrize("alias", ["image-nano-banana", "image-qwen"])
def test_a_reply_without_an_image_is_a_media_error(alias):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "I cannot draw that"}}], "data": []})

    with pytest.raises(media.MediaError):
        media.generate_image(alias, "a diagram", client=_client(handler))


def test_a_reply_without_audio_is_a_media_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "audio/pcm;rate=24000"})

    with pytest.raises(media.MediaError):
        media.synthesize_speech("voice-gemini-lite", "hi", client=_client(handler))


def test_a_provider_refusal_is_not_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "No allowed providers are available", "code": 404}})

    with pytest.raises(ValueError, match="No allowed providers are available"):
        media.synthesize_speech("voice-heygen", "hi", client=_client(handler))


@pytest.mark.parametrize("status", [429, 502, 503])
def test_a_busy_provider_is_a_media_error_so_the_job_retries(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "busy"}})

    with pytest.raises(media.MediaError):
        media.generate_image("image-qwen", "a diagram", client=_client(handler))


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"rest"


@pytest.mark.parametrize(("data", "mime"), [
    (b"\x89PNG\r\n\x1a\n....", "image/png"),
    (b"\xff\xd8\xff\xe0....", "image/jpeg"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
])
def test_the_images_endpoint_mime_comes_from_the_magic_bytes(data, mime):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data).decode()}]})

    assert media.generate_image("image-qwen", "x", client=_client(handler)).mime == mime


def test_a_data_url_without_a_media_type_falls_back_to_the_magic_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        url = "data:;base64," + base64.b64encode(b"\xff\xd8\xff\xe0....").decode()
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {"url": url}}]}}]})

    assert media.generate_image("image-nano-banana", "x", client=_client(handler)).mime == "image/jpeg"


def test_a_chat_image_that_is_not_a_data_url_is_a_value_error():
    """Fetching a remote URL is not supported, and retrying would get the same reply."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {"url": "https://x.test/a.png"}}]}}]})

    with pytest.raises(ValueError, match="data URL"):
        media.generate_image("image-nano-banana", "x", client=_client(handler))


def test_speech_cost_is_estimated_from_the_characters_when_the_provider_gives_none():
    text = "a" * 2000
    speech = media.synthesize_speech("voice-heygen", text, client=_client(_pcm_reply("audio/pcm;rate=44100")))

    assert speech.cost == pytest.approx(2 * media.VOICES["voice-heygen"].usd_per_1k_chars_estimate)


def test_a_4xx_message_names_the_http_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "No allowed providers"}})

    with pytest.raises(ValueError, match=r"^HTTP 404: No allowed providers"):
        media.synthesize_speech("voice-heygen", "hi", client=_client(handler))


def _busy(headers):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}}, headers=headers)
    return handler


def test_a_busy_reply_carries_the_wait_the_provider_asks_for():
    with pytest.raises(media.MediaError) as busy:
        media.generate_image("image-qwen", "a diagram", client=_client(_busy({"Retry-After": "7"})))
    assert busy.value.retry_after == 7
    assert media.retry_delay(busy.value, attempt=1) == 7


def test_a_retry_after_given_as_a_date_is_read_as_seconds_from_now():
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)

    with pytest.raises(media.MediaError) as busy:
        media.synthesize_speech("voice-heygen", "hi", client=_client(_busy({"Retry-After": when})))

    assert 15 <= busy.value.retry_after <= 20


@pytest.mark.parametrize("header", [{}, {"Retry-After": "soon"}, {"Retry-After": "NaN"},
                                    {"Retry-After": "inf"}, {"Retry-After": "-5"}])
def test_without_a_usable_retry_after_the_wait_is_a_short_backoff(header):
    with pytest.raises(media.MediaError) as busy:
        media.generate_image("image-qwen", "a diagram", client=_client(_busy(header)))
    assert busy.value.retry_after is None
    assert media.retry_delay(busy.value, attempt=1) == media.RETRY_WAIT_SECONDS


def test_a_long_retry_after_is_capped():
    """A job holds a worker while it waits; past the cap the scene fails and the lecturer can try later."""
    assert media.retry_delay(media.MediaError("busy", retry_after=3600), attempt=1) == media.MAX_RETRY_WAIT_SECONDS
    assert media.retry_delay(media.MediaError("busy", retry_after=-5), attempt=1) == 0


def test_waiting_sleeps_for_the_delay(media_waits):
    media.wait_before_retry(media.MediaError("busy", retry_after=3), attempt=1)
    assert media_waits == [3]
