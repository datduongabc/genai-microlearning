"""Shared cross-family guard for research judges (CLI and studies)."""

# Hãng theo tiền tố tên model. Không đọc từ tiền tố nhà cung cấp của litellm, vì Groq phục vụ model
# của nhiều hãng khác nhau.
_FAMILY_BY_NAME = (
    ("gemini", "google"),
    ("gemma", "google"),
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("llama", "meta"),
    ("qwen", "alibaba"),
)


def model_family(litellm_model: str) -> str:
    parts = litellm_model.lower().split("/")
    for prefix, family in _FAMILY_BY_NAME:
        if parts[-1].startswith(prefix):
            return family
    # "groq/<hãng>/<model>" thì đoạn giữa là hãng; còn lại lấy nhà cung cấp.
    return parts[-2] if len(parts) >= 3 else parts[0]



def refuse_same_family(judge: str, generators: list[tuple[str, str | None, list]], model_info: list[dict]) -> str:
    models = {m["model_name"]: m.get("litellm_params", {}).get("model", "") for m in model_info}
    if judge not in models:
        raise ValueError(f"Gateway không có alias chấm '{judge}'. Các alias hiện có: {', '.join(sorted(models))}")
    judge_family = model_family(models[judge])
    for label, generator_model, generation_models in generators:
        if generation_models:
            if any(model_family(call["served_model"]) == judge_family for call in generation_models):
                raise ValueError(f"{label}: mô hình chấm cùng hãng với model thực sự đã sinh gói")
            continue
        if generator_model not in models:
            raise ValueError(
                f"{label}: gateway không có alias '{generator_model}' đã sinh gói này, nên không "
                "kiểm được mô hình chấm có khác hãng hay không."
            )
        if model_family(models[generator_model]) == judge_family:
            raise ValueError(
                f"Từ chối: mô hình chấm '{judge}' cùng hãng {judge_family} với model "
                f"'{generator_model}' đã sinh {label}. Chọn mô hình chấm của hãng khác."
            )
    return models[judge]

