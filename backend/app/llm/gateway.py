"""Provider-agnostic LLM calls. Every request goes to the LLM gateway (LiteLLM
Proxy, see devops/gateway/config.yaml), which holds the provider API keys and
maps a model alias to a real provider/model, so swapping models or providers is
a configuration change, not a code change.
"""

import litellm
from pydantic import BaseModel

from app.config import settings


def _active_model() -> str:
    return settings.llm_model_demo if settings.llm_use_demo_model else settings.llm_model_dev


def _completion(**kwargs):
    # "openai/" only means "speak the OpenAI-compatible protocol to api_base";
    # the gateway decides which provider actually serves the alias.
    return litellm.completion(
        model=f"openai/{_active_model()}",
        api_base=settings.llm_gateway_url,
        api_key=settings.llm_gateway_key,
        **kwargs,
    )


def complete(prompt: str, system_instruction: str | None = None, **kwargs) -> str:
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    response = _completion(messages=messages, **kwargs)
    return response.choices[0].message.content


def transcribe_audio(audio_base64: str, audio_format: str, prompt: str) -> str:
    """Phiên âm audio/video qua Gemini multimodal (litellm content type
    `input_audio`, map sang Part.from_bytes của Gemini -- xem
    litellm/llms/vertex_ai/gemini/transformation.py). Không dùng dịch vụ ASR
    riêng (vd Speechmatics) -- giữ đúng triết lý "mọi lệnh gọi LLM qua
    litellm, free-tier" đã áp dụng cho toàn bộ pipeline sinh nội dung, đổi
    lại không có speaker diarization/confidence score chi tiết."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "input_audio", "input_audio": {"data": audio_base64, "format": audio_format}},
        ],
    }]
    response = _completion(messages=messages)
    return response.choices[0].message.content


def complete_structured[T: BaseModel](
    prompt: str, response_model: type[T], system_instruction: str | None = None, **kwargs
) -> T:
    """JSON-schema-constrained completion -- litellm maps `response_format`
    to each provider's native structured-output mechanism (Gemini's
    response_schema, OpenAI's json_schema, ...)."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    response = _completion(messages=messages, response_format=response_model, **kwargs)
    return response_model.model_validate_json(response.choices[0].message.content)
