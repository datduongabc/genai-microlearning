"""Provider-agnostic LLM calls. Every request goes to the LLM gateway (LiteLLM
Proxy, see devops/gateway/config.yaml), which holds the provider API keys and
maps a model alias to a real provider/model, so swapping models or providers is
a configuration change, not a code change.
"""

import contextlib
import contextvars
import json

import litellm
from litellm.constants import RETURN_RAW_MODEL_NAME_METADATA_KEY
from pydantic import BaseModel

from app.config import settings

# Model do giảng viên chọn cho riêng một lượt sinh. Để trong ContextVar chứ không truyền
# qua tham số: một lượt sinh đi qua mười chỗ gọi rải khắp generation, chat và ingestion,
# nên truyền tay sẽ phải thêm một tham số vào chữ ký của cả pipeline chỉ để chuyển tiếp.
# ContextVar cũng là thứ duy nhất an toàn ở đây: worker chạy nhiều lượt sinh đồng thời,
# mà một biến toàn cục thì lượt này sẽ đổi model của lượt kia.
_model_da_chon: contextvars.ContextVar[str | None] = contextvars.ContextVar("_model_da_chon", default=None)
_experiment: contextvars.ContextVar[bool] = contextvars.ContextVar("_experiment", default=False)
_model_calls: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("_model_calls", default=None)


@contextlib.contextmanager
def experiment_mode():
    """Pin an evaluation run, including nested model selections, to its chosen alias."""
    token = _experiment.set(True)
    try:
        yield
    finally:
        _experiment.reset(token)


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
    token = _model_da_chon.set(alias)
    try:
        yield
    finally:
        # reset trong finally: một lượt sinh ném ngoại lệ không được để lượt kế tiếp
        # chạy trên model của người khác.
        _model_da_chon.reset(token)


def active_model() -> str:
    da_chon = _model_da_chon.get()
    if da_chon:
        return da_chon
    return settings.llm_model


def _completion(*, small_step: bool = False, **kwargs):
    alias = active_model()
    routed_alias = alias
    pinned = settings.llm_experiment_mode or _experiment.get()
    extra_body = dict(kwargs.pop("extra_body", None) or {})
    # Keep the provider model in the HTTP response instead of the proxy's routing alias.
    extra_body["metadata"] = {
        **extra_body.get("metadata", {}), RETURN_RAW_MODEL_NAME_METADATA_KEY: True,
    }
    if pinned:
        if alias == "gemini-balanced" or alias.endswith("-small"):
            raise ValueError("Experiments require a single-model alias, not a routing profile")
        extra_body["disable_fallbacks"] = True
    elif small_step and alias in {"gemini-fast", "gemini-flash"}:
        # These profiles alone permit Groq. Reserve output and schema tokens too;
        # Content never opts in, even in the no-RAG arm with a short prompt.
        schema = kwargs.get("response_format")
        schema_text = json.dumps(schema.model_json_schema() if isinstance(schema, type) else schema)
        input_tokens = litellm.token_counter(model="gpt-4o", messages=kwargs["messages"])
        input_tokens += litellm.token_counter(model="gpt-4o", text=schema_text)
        output_tokens = kwargs.get("max_tokens", 2048)
        if output_tokens <= 2048 and input_tokens + output_tokens <= 6000:
            routed_alias = f"{alias}-small"
            kwargs["max_tokens"] = output_tokens
    # "openai/" only means "speak the OpenAI-compatible protocol to api_base";
    # the gateway decides which provider actually serves the alias.
    response = litellm.completion(
        model=f"openai/{routed_alias}",
        api_base=settings.llm_gateway_url,
        api_key=settings.llm_gateway_key,
        extra_body=extra_body,
        **kwargs,
    )
    calls = _model_calls.get()
    served = getattr(response, "model", None)
    if calls is not None and isinstance(served, str):
        calls.append({"requested_alias": alias, "routed_alias": routed_alias, "served_model": served})
    return response


def complete(prompt: str, system_instruction: str | None = None, **kwargs) -> str:
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
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
        )
    return response.segments or []


def complete_structured[T: BaseModel](
    prompt: str, response_model: type[T], system_instruction: str | None = None,
    response_format: dict | None = None, **kwargs
) -> T:
    """JSON-schema-constrained completion -- litellm maps `response_format`
    to each provider's native structured-output mechanism (Gemini's
    response_schema, OpenAI's json_schema, ...). `response_format` can constrain
    the schema for this call, while the reply is still parsed as `response_model`."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    response = _completion(
        messages=messages,
        response_format=response_format if response_format is not None else response_model,
        **kwargs,
    )
    # strict=False: model hay để lọt tab, xuống dòng trần vào giữa chuỗi JSON khi viết công thức, và
    # trình đọc nghiêm từ chối cả câu trả lời dù nó vẫn đọc được. Schema vẫn được kiểm đầy đủ ở bước sau.
    return response_model.model_validate(json.loads(response.choices[0].message.content, strict=False))
