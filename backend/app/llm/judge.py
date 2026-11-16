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
    ("minimax", "minimax"),
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


# In order of preference; eligible_judges drops every one of a generator's family, so each generator
# keeps at least two judges of other families.
JUDGE_POOL = ("claude-fast", "openai-fast", "gemini-flash", "deepseek-flash")


def eligible_judges(generators: list[tuple[str, str | None, list]], model_info: list[dict]) -> list[dict]:
    """Use the same provenance guard for initial selection and substitutes."""
    candidates = []
    for alias in JUDGE_POOL:
        if not any(m["model_name"] == alias for m in model_info):
            continue
        try:
            model = refuse_same_family(alias, generators, model_info)
        except ValueError:
            continue
        declared = next(m for m in model_info if m["model_name"] == alias).get("model_info") or {}
        candidates.append({"alias": alias, "model": model,
                           "max_input_tokens": declared.get("max_input_tokens")})
    return candidates


def is_quota_error(exc: Exception) -> bool:
    import litellm
    return isinstance(exc, litellm.RateLimitError) or getattr(exc, "status_code", None) == 429
