"""Provider-agnostic LLM calls. Every request goes to the LLM gateway (LiteLLM
Proxy, see devops/gateway/config.yaml), which holds the provider API keys and
maps a model alias to a real provider/model, so swapping models or providers is
a configuration change, not a code change.
"""

import base64
import contextlib
import contextvars
import json
import math
import random
import re
import time
from collections.abc import AsyncIterator

import httpx
import litellm
import openai
from litellm.constants import RETURN_RAW_MODEL_NAME_METADATA_KEY
from pydantic import BaseModel, ValidationError

from app import timing
from app.config import settings

# A 429 that says the account has no money or the wrong plan: waiting will not help.
_NOT_A_RATE_LIMIT = re.compile(r"insufficient_quota|billing|credit balance|no credits", re.IGNORECASE)
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
# The gateway owns retries and fallbacks (devops/gateway/config.yaml router_settings): 120 s x 2 tries x 3 routes
# in the longest chain. Without this the client retried the whole chain itself and waited up to 100 minutes.
GATEWAY_TIMEOUT_SECONDS = 780
# For a call made while a web request waits on it: CloudFront answers 504 at 60 s (infra cdn.tf), and a call still
# running after that only holds one of the API task's request slots. The job path keeps GATEWAY_TIMEOUT_SECONDS.
REQUEST_TIMEOUT_SECONDS = 55

# Model do giảng viên chọn cho riêng một lượt sinh. Để trong ContextVar chứ không truyền
# qua tham số: một lượt sinh đi qua mười chỗ gọi rải khắp generation, chat và ingestion,
# nên truyền tay sẽ phải thêm một tham số vào chữ ký của cả pipeline chỉ để chuyển tiếp.
# ContextVar cũng là thứ duy nhất an toàn ở đây: worker chạy nhiều lượt sinh đồng thời,
# mà một biến toàn cục thì lượt này sẽ đổi model của lượt kia.
_selected_model: contextvars.ContextVar[str | None] = contextvars.ContextVar("_selected_model", default=None)
_experiment: contextvars.ContextVar[bool] = contextvars.ContextVar("_experiment", default=False)
_model_calls: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("_model_calls", default=None)
_waits_on_rate_limit: contextvars.ContextVar[bool] = contextvars.ContextVar("_waits_on_rate_limit", default=False)


@contextlib.contextmanager
def waiting_on_rate_limits():
    """For background jobs: a 429 waits inside the call. A request keeps failing fast, since someone is waiting on it."""
    token = _waits_on_rate_limit.set(True)
    try:
        yield
    finally:
        _waits_on_rate_limit.reset(token)


@contextlib.contextmanager
def experiment_mode():
    """Pin an evaluation run, including nested model selections, to its chosen alias."""
    token = _experiment.set(True)
    try:
        yield
    finally:
        _experiment.reset(token)


def pinned() -> bool:
    """Whether calls must stay on the alias they name, with no gateway fallback."""
    return settings.llm_experiment_mode or _experiment.get()


@contextlib.contextmanager
def capture_models():
    calls: list[dict] = []
    token = _model_calls.set(calls)
    try:
        yield calls
    finally:
        _model_calls.reset(token)


def recorded_models() -> list[dict]:
    return list(_model_calls.get() or [])


@contextlib.contextmanager
def using_model(alias: str | None):
    """Mọi lệnh gọi LLM bên trong khối này dùng `alias`. Truyền None nghĩa là giữ nguyên
    cấu hình, để chỗ gọi không phải tự phân nhánh khi giảng viên chưa chọn gì."""
    token = _selected_model.set(alias)
    try:
        yield
    finally:
        # reset trong finally: một lượt sinh ném ngoại lệ không được để lượt kế tiếp
        # chạy trên model của người khác.
        _selected_model.reset(token)


def requested_model() -> str | None:
    return _selected_model.get()


def active_model() -> str:
    selected = requested_model()
    if selected:
        return selected
    return settings.llm_model


def _rate_limit_wait(error: litellm.RateLimitError, attempt: int) -> float:
    """The provider's Retry-After when it sends one, otherwise doubling from 5 s. Jittered, so jobs that
    were limited together do not all come back in the same second."""
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        wait = float(headers.get("retry-after", ""))
    except ValueError:
        wait = 5.0 * 2**attempt
    return min(wait, MAX_RATE_LIMIT_WAIT_SECONDS) * random.uniform(1.0, 1.5)


def _response_cost(response) -> float | None:
    """What the gateway says the call cost, in USD. LiteLLM fills `x-litellm-response-cost` from OpenRouter's
    own `usage.cost` and only falls back to its price map when the provider sends none. Unknown stays None:
    a zero would read as a free call, and a header such as "None" is the proxy saying it has no price."""
    headers = (getattr(response, "_hidden_params", None) or {}).get("additional_headers") or {}
    try:
        cost = float(headers.get("llm_provider-x-litellm-response-cost", ""))
    except (TypeError, ValueError):
        return None
    return cost if math.isfinite(cost) and cost >= 0 else None


def _never_connected(error: BaseException) -> bool:
    # litellm wraps openai's error, which wraps httpx's. Only a connection that was never made reached
    # no model; a timeout or a reply cut off after the request was sent may have been billed.
    while error is not None:
        if isinstance(error, openai.APIConnectionError) and not isinstance(error, openai.APITimeoutError):
            return isinstance(error.__cause__, httpx.ConnectError)
        error = error.__cause__ or error.__context__
    return False


def _call_with_rate_limit_retries(alias: str, **request):
    retries = settings.llm_rate_limit_retries if _waits_on_rate_limit.get() else 0
    attempt = 0
    reconnected = False
    while True:
        with timing.timed("llm", requested_alias=alias) as span:
            try:
                response = litellm.completion(**request)
            except litellm.RateLimitError as error:
                span["rate_limited"] = True
                if attempt == retries or _NOT_A_RATE_LIMIT.search(str(error)):
                    raise
                wait = _rate_limit_wait(error, attempt)
                attempt += 1
            except Exception as error:
                # With max_retries=0 the client no longer retries a gateway that is restarting: one more
                # try, once, costs nothing, where a failed call requeues the whole job.
                if reconnected or not _never_connected(error):
                    raise
                reconnected = True
                wait = 2
            else:
                usage = getattr(response, "usage", None)
                span["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
                span["completion_tokens"] = getattr(usage, "completion_tokens", None)
                span["served_model"] = getattr(response, "model", None)
                span["cost_usd"] = _response_cost(response)
                return response
        with timing.timed("llm_backoff"):
            time.sleep(wait)


def _completion(**kwargs):
    alias = active_model()
    extra_body = dict(kwargs.pop("extra_body", None) or {})
    # Keep the provider model in the HTTP response instead of the proxy's routing alias.
    extra_body["metadata"] = {
        **extra_body.get("metadata", {}), RETURN_RAW_MODEL_NAME_METADATA_KEY: True,
    }
    if pinned():
        extra_body["disable_fallbacks"] = True
    # "openai/" only means "speak the OpenAI-compatible protocol to api_base";
    # the gateway decides which provider actually serves the alias.
    response = _call_with_rate_limit_retries(
        alias,
        model=f"openai/{alias}",
        api_base=settings.llm_gateway_url,
        api_key=settings.llm_gateway_key,
        extra_body=extra_body,
        # A caller such as the study judge may set its own, shorter bound.
        **{"max_retries": 0, "timeout": GATEWAY_TIMEOUT_SECONDS, **kwargs},
    )
    calls = _model_calls.get()
    served = getattr(response, "model", None)
    if calls is not None and isinstance(served, str):
        calls.append({"requested_alias": alias, "served_model": served})
    return response


async def stream_text(prompt: str, system_instruction: str | None = None, *, alias: str | None = None,
                      usage: dict | None = None) -> AsyncIterator[str]:
    """The reply piece by piece, without holding a thread while it waits. Retries stay with the gateway: once a
    word has reached the reader, the reply cannot start over. `alias` overrides the chosen model for one call;
    `usage`, when given, receives the served model, the token counts and the seconds to the first piece."""
    alias = alias or active_model()
    extra_body = {"disable_fallbacks": True} if pinned() else {}
    messages = [{"role": "system", "content": system_instruction}] if system_instruction else []
    messages.append({"role": "user", "content": prompt})
    start = time.perf_counter()
    # Someone is reading: on a stream the timeout bounds each wait for the next piece, not the whole reply,
    # so a long answer still finishes, while a chain whose routes all fail no longer holds the reader for
    # the job path's 13 minutes (seen on 2026-11-25 with OpenRouter refusing and Google overloaded).
    stream = await litellm.acompletion(
        model=f"openai/{alias}", api_base=settings.llm_gateway_url, api_key=settings.llm_gateway_key,
        extra_body=extra_body, messages=messages, stream=True, stream_options={"include_usage": True},
        max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if usage is not None:
        usage["requested_alias"] = alias
    async for chunk in stream:
        if usage is not None:
            if served := getattr(chunk, "model", None):
                usage["served_model"] = served
            if counted := getattr(chunk, "usage", None):
                usage["prompt_tokens"] = getattr(counted, "prompt_tokens", None)
                usage["completion_tokens"] = getattr(counted, "completion_tokens", None)
        text = chunk.choices[0].delta.content if chunk.choices else None
        if text:
            if usage is not None and "first_token_seconds" not in usage:
                usage["first_token_seconds"] = round(time.perf_counter() - start, 3)
            yield text
    if usage is not None:
        usage["seconds"] = round(time.perf_counter() - start, 3)


def complete(prompt: str, system_instruction: str | None = None, **kwargs) -> str:
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    response = _completion(messages=messages, **kwargs)
    return response.choices[0].message.content


def complete_with_image(prompt: str, png: bytes, **kwargs) -> str:
    """One question about one image, routed like complete(): the alias must serve a vision model."""
    image = f"data:image/png;base64,{base64.b64encode(png).decode()}"
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image}},
    ]}]
    response = _completion(messages=messages, **kwargs)
    return response.choices[0].message.content


def transcribe_audio(path: str) -> list[dict]:
    """Whisper segments through the gateway, independent of the chosen chat model."""
    with open(path, "rb") as audio:
        response = litellm.transcription(
            model="openai/transcribe",
            file=audio,
            api_base=settings.llm_gateway_url,
            api_key=settings.llm_gateway_key,
            response_format="verbose_json",
            max_retries=0,
            timeout=180,
        )
    return response.segments or []


# Extra calls for a reply that is not valid JSON or breaks the schema (cut off, missing field). It is
# sampled again, so a one-off slip passes; a model that keeps failing still fails, after this bound.
MALFORMED_REPLY_RETRIES = 2
_REPLY_BROKE_SCHEMA = re.compile(r"does not match the expected schema|failed_generation|json_validate_failed")


def complete_structured[T: BaseModel](
    prompt: str, response_model: type[T], system_instruction: str | None = None,
    response_format: dict | None = None, validation_context: dict | None = None, **kwargs
) -> T:
    """JSON-schema-constrained completion -- litellm maps `response_format`
    to each provider's native structured-output mechanism (Gemini's
    response_schema, OpenAI's json_schema, ...). `response_format` can constrain
    the schema for this call, while the reply is still parsed as `response_model`.
    `validation_context` reaches the model's validators, so a reply that breaks a rule the schema
    cannot state (one depending on the prompt) is asked again like a malformed one."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    for attempt in range(MALFORMED_REPLY_RETRIES + 1):
        try:
            response = _completion(
                messages=messages,
                response_format=response_format if response_format is not None else response_model,
                **kwargs,
            )
        except litellm.BadRequestError as error:
            # A provider that checks the schema itself refuses a bad reply here instead of returning it.
            if attempt == MALFORMED_REPLY_RETRIES or not _REPLY_BROKE_SCHEMA.search(str(error)):
                raise
            continue
        choice = response.choices[0]
        if choice.message.content is None:
            # A thinking model that spends its whole output budget thinking answers with nothing; asking
            # again under the same cap would pay for the same. Said plainly instead of json.loads(None).
            raise ValueError(f"The model returned no answer (finish_reason={choice.finish_reason}): "
                             "its output limit ran out before it wrote one")
        try:
            # strict=False: model hay để lọt tab, xuống dòng trần vào giữa chuỗi JSON khi viết công
            # thức, và trình đọc nghiêm từ chối cả câu trả lời dù nó vẫn đọc được. Schema vẫn được
            # kiểm đầy đủ ngay sau đó.
            return response_model.model_validate(json.loads(choice.message.content, strict=False),
                                                 context=validation_context)
        except (json.JSONDecodeError, ValidationError):
            # Both are ValueError, which the job queue treats as permanent: without asking again here,
            # one bad reply late in a module would throw away every paid call made before it.
            if attempt == MALFORMED_REPLY_RETRIES:
                raise
    raise AssertionError("unreachable")
