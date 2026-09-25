"""Provider-agnostic LLM calls. Every request goes to the LLM gateway (LiteLLM
Proxy, see devops/gateway/config.yaml), which holds the provider API keys and
maps a model alias to a real provider/model, so swapping models or providers is
a configuration change, not a code change.
"""

import contextlib
import contextvars
import json

import litellm
from pydantic import BaseModel

from app.config import settings

# Model do giảng viên chọn cho riêng một lượt sinh. Để trong ContextVar chứ không truyền
# qua tham số: một lượt sinh đi qua mười chỗ gọi rải khắp generation, chat và ingestion,
# nên truyền tay sẽ phải thêm một tham số vào chữ ký của cả pipeline chỉ để chuyển tiếp.
# ContextVar cũng là thứ duy nhất an toàn ở đây: worker chạy nhiều lượt sinh đồng thời,
# mà một biến toàn cục thì lượt này sẽ đổi model của lượt kia.
_model_da_chon: contextvars.ContextVar[str | None] = contextvars.ContextVar("_model_da_chon", default=None)


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


def _active_model() -> str:
    da_chon = _model_da_chon.get()
    if da_chon:
        return da_chon
    return settings.llm_model


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
    # strict=False: model hay để lọt tab, xuống dòng trần vào giữa chuỗi JSON khi viết công thức, và
    # trình đọc nghiêm từ chối cả câu trả lời dù nó vẫn đọc được. Schema vẫn được kiểm đầy đủ ở bước sau.
    return response_model.model_validate(json.loads(response.choices[0].message.content, strict=False))
