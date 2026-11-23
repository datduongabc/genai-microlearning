"""Consistency of the gateway setup (devops/gateway, devops/docker-compose.yml) with
the backend. These files are never executed by the test suite, so a typo in a
model alias or an accidentally committed key would otherwise only show up as a
failed LLM call."""

import json
import re
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_CONFIG = REPO_ROOT / "devops" / "gateway" / "config.yaml"
GATEWAY_DOCKERFILE = REPO_ROOT / "devops" / "gateway" / "Dockerfile"
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"
BACKEND_LOCK = REPO_ROOT / "backend" / "uv.lock"

# Every remote model goes through OpenRouter, Whisper included.
SUPPORTED_PROVIDERS = {"openrouter"}
PROVIDER_KEY_VARIABLES = ("OPENROUTER_API_KEY",)


@pytest.fixture(scope="module")
def gateway():
    return yaml.safe_load(GATEWAY_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _deployments(gateway) -> list[dict]:
    return gateway["model_list"]


def _remote_providers(gateway) -> set[str]:
    """Whisper is reached over the OpenAI protocol ("openai/..." with an api_base), but no key of
    OpenAI's is involved: an OpenRouter api_base counts as OpenRouter."""
    providers = set()
    for d in _deployments(gateway):
        params = d["litellm_params"]
        if "api_base" not in params:
            providers.add(params["model"].split("/")[0])
        elif params["api_base"].startswith("https://openrouter.ai/"):
            providers.add("openrouter")
    return providers


def test_the_default_alias_of_the_backend_is_defined_in_the_gateway(gateway):
    defined = {d["model_name"] for d in _deployments(gateway)}

    assert Settings.model_fields["llm_model"].default in defined


def test_every_model_id_is_pinned(gateway):
    """Google silently moves a "-latest" alias to each new release, so a result measured on
    it cannot be reproduced later."""
    floating = [d["litellm_params"]["model"] for d in _deployments(gateway) if d["litellm_params"]["model"].endswith("-latest")]

    assert floating == []


def test_only_the_supported_providers_are_routed(gateway):
    providers = _remote_providers(gateway)

    assert providers <= SUPPORTED_PROVIDERS


def test_every_supported_provider_is_reachable_through_an_alias(gateway):
    providers = _remote_providers(gateway)

    assert providers == SUPPORTED_PROVIDERS


def test_no_secret_is_written_in_the_gateway_config(gateway):
    keys = [d["litellm_params"]["api_key"] for d in _deployments(gateway) if "api_key" in d["litellm_params"]]
    keys.append(gateway["general_settings"]["master_key"])

    assert keys, "expected at least the master key to be configured"
    assert all(str(k).startswith("os.environ/") for k in keys)


def _deployment(gateway, alias: str) -> dict:
    return next(d for d in _deployments(gateway) if d["model_name"] == alias)


def test_no_model_runs_in_the_stack(gateway, compose):
    """Text, transcription and embeddings all come from providers: no alias points inside the
    stack, and no service serves a model."""
    local = [d["model_name"] for d in _deployments(gateway)
             if "api_base" in d["litellm_params"] and not d["litellm_params"]["api_base"].startswith("https://openrouter.ai/")]

    assert local == []
    assert not {name for name in compose["services"] if name in {"embedder", "reranker"} or "ollama" in name}


def test_the_default_embedding_alias_is_a_provider_model(gateway):
    deployment = _deployment(gateway, Settings.model_fields["embedding_model"].default)

    assert deployment["litellm_params"]["model"].startswith("openrouter/")


def test_the_default_embedding_alias_is_never_offered_as_a_chat_model(gateway):
    """The model picker lists only aliases whose mode is "chat"."""
    deployment = _deployment(gateway, Settings.model_fields["embedding_model"].default)

    assert deployment["model_info"]["mode"] == "embedding"


def test_the_gateway_is_only_published_on_the_loopback_interface(compose):
    ports = compose["services"]["gateway"].get("ports", [])

    assert all(str(p).startswith("127.0.0.1:") for p in ports)


def test_the_backend_receives_no_provider_api_key(compose):
    """The gateway is the only holder of provider keys. The backend is given its
    variables one by one (no env_file that would hand it the whole .env), and none of
    them is a provider key."""
    backend = compose["services"]["backend"]

    assert "env_file" not in backend
    assert not any(variable in backend["environment"] for variable in PROVIDER_KEY_VARIABLES)


def test_the_gateway_receives_the_provider_keys_and_its_own_key(compose):
    environment = compose["services"]["gateway"]["environment"]

    assert all(variable in environment for variable in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"))


def test_the_backend_talks_to_the_gateway_and_waits_for_it(compose):
    backend = compose["services"]["backend"]

    assert backend["environment"]["LLM_GATEWAY_URL"] == "http://gateway:4000"
    assert backend["depends_on"]["gateway"]["condition"] == "service_healthy"


def test_the_gateway_runs_the_same_litellm_version_as_the_backend():
    """Client and gateway must map requests/responses the same way."""
    lock = BACKEND_LOCK.read_text(encoding="utf-8")
    locked = re.search(r'name = "litellm"\nversion = "([^"]+)"', lock).group(1)
    pinned = re.search(r"ARG LITELLM_VERSION=(\S+)", GATEWAY_DOCKERFILE.read_text(encoding="utf-8")).group(1)

    assert pinned == locked


def test_every_alias_has_a_label_for_the_teacher(gateway):
    """The model picker shows this label; without one the teacher reads a raw alias like
    "claude-fast" and cannot tell which model it is.

    Required on every alias, embedding ones included, although those never reach the
    picker: the gateway decides which aliases are chat models at runtime, and this test
    runs without a gateway, so the only rule it can check without guessing from alias
    names is "every alias is labelled"."""
    unlabelled = [
        d["model_name"] for d in _deployments(gateway)
        if not (d.get("model_info") or {}).get("display_name")
    ]

    assert unlabelled == []


def test_transcription_alias_routes_to_whisper_through_openrouter(gateway):
    """LiteLLM has no OpenRouter transcription route, so Whisper is reached over the OpenAI
    protocol at OpenRouter's base URL, which returns the segment timestamps citations need."""
    deployment = next(d for d in _deployments(gateway) if d["model_name"] == "transcribe")
    assert deployment["litellm_params"] == {
        "model": "openai/openai/whisper-large-v3-turbo",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "os.environ/OPENROUTER_API_KEY",
    }
    assert deployment["model_info"]["mode"] == "audio_transcription"


def test_router_accepts_the_config(gateway, monkeypatch):
    from litellm import Router

    for name in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"):
        monkeypatch.setenv(name, "test-key")
    router = Router(model_list=gateway["model_list"], **gateway["router_settings"])
    assert router.routing_strategy == "simple-shuffle"
    assert router.num_retries == 1
    assert router.disable_cooldowns is True
    aliases = {d["model_name"] for d in gateway["model_list"]}
    for fallback_type in (router.fallbacks, router.context_window_fallbacks):
        for mapping in fallback_type:
            for alias, backups in mapping.items():
                assert alias in aliases and set(backups) <= aliases
    router.reset()


def test_provider_429_uses_the_configured_fallback(gateway, monkeypatch):
    import asyncio

    import litellm

    for name in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"):
        monkeypatch.setenv(name, "test-key")
    attempts = []
    async def provider(**kwargs):
        attempts.append(kwargs["model"])
        if kwargs["model"] == "openrouter/anthropic/claude-haiku-5.5":
            raise litellm.RateLimitError("quota exhausted", model=kwargs["model"], llm_provider="openrouter")
        return litellm.ModelResponse(model=kwargs["model"], choices=[
            {"message": {"role": "assistant", "content": "quota fallback"}},
        ])
    monkeypatch.setattr(litellm, "acompletion", provider)
    router = litellm.Router(model_list=gateway["model_list"], **gateway["router_settings"])
    response = asyncio.run(router.acompletion(
        model="claude-fast", messages=[{"role": "user", "content": "hello"}], num_retries=0,
    ))
    assert response.model == "openrouter/google/gemini-3.8-flash"
    assert attempts == ["openrouter/anthropic/claude-haiku-5.5", "openrouter/google/gemini-3.8-flash"]
    router.reset()


def test_the_default_never_falls_back_to_a_paid_opt_in_model(gateway, monkeypatch):
    import asyncio

    import litellm

    for name in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"):
        monkeypatch.setenv(name, "test-key")
    attempts = []
    async def provider(**kwargs):
        attempts.append(kwargs["model"])
        raise litellm.RateLimitError("quota exhausted", model=kwargs["model"], llm_provider="gemini")
    monkeypatch.setattr(litellm, "acompletion", provider)
    router = litellm.Router(model_list=gateway["model_list"], **gateway["router_settings"])
    with pytest.raises(litellm.RateLimitError):
        asyncio.run(router.acompletion(
            model="gemini-flash", messages=[{"role": "user", "content": "hello"}],
            max_tokens=2048, num_retries=0,
        ))
    assert attempts and all("gemini" in model for model in attempts)
    router.reset()


PAID_ALIASES = {"claude-fast", "openai-fast", "deepseek-flash"}


def test_evaluation_paid_aliases_are_current_and_never_fallback_targets(gateway):
    expected = {
        "claude-fast": "openrouter/anthropic/claude-haiku-5.5",
        "openai-fast": "openrouter/openai/gpt-6-luna",
        "deepseek-flash": "openrouter/deepseek/deepseek-v4.1-flash",
    }
    for alias, model in expected.items():
        deployments = [d for d in gateway["model_list"] if d["model_name"] == alias]
        assert len(deployments) == 1
        assert deployments[0]["litellm_params"]["model"] == model
    for setting in ("fallbacks", "context_window_fallbacks"):
        chains = {alias: backups for chain in gateway["router_settings"][setting] for alias, backups in chain.items()}
        for alias in expected:
            assert chains[alias] == ["gemini-flash"]
        assert not PAID_ALIASES & {backup for backups in chains.values() for backup in backups}


def test_paid_alias_falls_back_to_gemini_and_experiment_flag_refuses_it(gateway, monkeypatch):
    import asyncio

    import litellm

    for name in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"):
        monkeypatch.setenv(name, "test-key")
    router = litellm.Router(model_list=gateway["model_list"], **gateway["router_settings"])
    for flag in ("mock_testing_fallbacks", "mock_testing_context_fallbacks"):
        call = {"model": "claude-fast", "messages": [{"role": "user", "content": "hello"}],
                flag: True, "mock_response": "free reply", "num_retries": 0}
        response = asyncio.run(router.acompletion(**call))
        assert "gemini" in response.model
        with pytest.raises((litellm.InternalServerError, litellm.ContextWindowExceededError)):
            asyncio.run(router.acompletion(**call, disable_fallbacks=True))
    router.reset()


SAMPLING_FIXED_ALIASES = ("claude-fast", "openai-fast")


def test_paid_generation_models_with_fixed_sampling_drop_the_temperature(gateway):
    """Claude Haiku 5.5 and GPT-6 Luna accept only temperature=1, while the pipeline asks
    for 0 at most steps. Without dropping it, an experiment run fails on its first call, and outside
    experiment mode every such call silently falls back to free Gemini: a module the lecturer asked
    Claude for came back mostly written by Gemini."""
    for alias in SAMPLING_FIXED_ALIASES:
        (deployment,) = [d for d in _deployments(gateway) if d["model_name"] == alias]
        assert "temperature" in deployment["litellm_params"].get("additional_drop_params", []), alias


@pytest.mark.parametrize("alias", SAMPLING_FIXED_ALIASES)
def test_a_pinned_paid_call_at_temperature_zero_is_served_by_its_own_model(gateway, monkeypatch, alias):
    import asyncio

    import litellm

    for name in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"):
        monkeypatch.setenv(name, "test-key")
    router = litellm.Router(model_list=gateway["model_list"], **gateway["router_settings"])
    expected = next(d for d in _deployments(gateway) if d["model_name"] == alias)["litellm_params"]["model"]

    response = asyncio.run(router.acompletion(
        model=alias, messages=[{"role": "user", "content": "hi"}], temperature=0.0,
        mock_response="ok", num_retries=0, disable_fallbacks=True,
    ))

    assert response.model == expected.split("/", 1)[1]
    router.reset()


def _openrouter_deployments(gateway) -> list[dict]:
    return [d for d in _deployments(gateway) if d["litellm_params"]["model"].startswith("openrouter/")]


def test_every_paid_model_goes_through_openrouter_with_its_one_key(gateway):
    """One balance and one key for every paid model; the provider keys of OpenAI and Anthropic
    are no longer read anywhere."""
    paid = {d["model_name"] for d in _openrouter_deployments(gateway)}

    assert PAID_ALIASES | {"gemini-flash", "embed-default"} == paid
    assert {d["litellm_params"]["api_key"] for d in _openrouter_deployments(gateway)} == {
        "os.environ/OPENROUTER_API_KEY"}


def test_each_openrouter_alias_is_pinned_to_one_upstream_that_keeps_no_prompt(gateway):
    """OpenRouter would otherwise pick the cheapest or fastest upstream per request, so two runs
    of one alias could be served by different hosts (and quantisations); a failing upstream must
    surface as an error, not be served silently elsewhere. Prompts carry course material, so
    only upstreams that neither store nor train on them are allowed."""
    for deployment in _openrouter_deployments(gateway):
        provider = deployment["litellm_params"]["extra_body"]["provider"]
        assert len(provider["order"]) == 1, deployment["model_name"]
        assert provider["allow_fallbacks"] is False, deployment["model_name"]
        assert provider["data_collection"] == "deny", deployment["model_name"]


def test_the_routing_preferences_reach_the_openrouter_request(gateway, monkeypatch):
    import asyncio

    import litellm

    for name in (*PROVIDER_KEY_VARIABLES, "LLM_GATEWAY_KEY"):
        monkeypatch.setenv(name, "test-key")
    sent = {}

    class Response:
        status_code = 200
        headers: ClassVar[dict] = {}

        def json(self):
            return {"id": "x", "object": "chat.completion", "created": 0, "model": "anthropic/claude-haiku-5.5",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

        text = ""

    async def post(self, url, *args, **kwargs):
        sent["url"] = url
        sent["body"] = kwargs.get("json") or json.loads(kwargs.get("data") or "{}")
        return Response()

    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

    monkeypatch.setattr(AsyncHTTPHandler, "post", post)
    router = litellm.Router(model_list=gateway["model_list"], **gateway["router_settings"])
    asyncio.run(router.acompletion(
        model="claude-fast", messages=[{"role": "user", "content": "hi"}], num_retries=0, disable_fallbacks=True,
    ))

    assert sent["url"].startswith("https://openrouter.ai/api/v1")
    assert sent["body"]["model"] == "anthropic/claude-haiku-5.5"
    assert sent["body"]["provider"] == {"order": ["anthropic"], "allow_fallbacks": False, "data_collection": "deny"}
    router.reset()


def test_every_openrouter_alias_declares_its_mode(gateway):
    """litellm's model table has no OpenRouter slugs, so the gateway reports no mode for them; the
    backend lists and accepts only aliases whose mode is "chat", so without it a paid alias vanished
    from the picker and generation refused it ("Gateway không phục vụ model sinh văn bản nào...")."""
    undeclared = [d["model_name"] for d in _openrouter_deployments(gateway)
                  if (d.get("model_info") or {}).get("mode") not in {"chat", "embedding"}]

    assert undeclared == []


def test_lecturers_are_offered_exactly_the_four_chosen_chat_models(gateway):
    """The four chat models the project chose to offer, all measured in
    docs/measurements/2026-11-16-four-chat-models.md. Offering another model means updating this set too."""
    from app.llm.catalog import to_choices

    offered = {c.alias for c in to_choices([
        {"model_name": d["model_name"], "litellm_params": d["litellm_params"], "model_info": d.get("model_info", {})}
        for d in _deployments(gateway)
    ])}
    assert offered == {"gemini-flash", "claude-fast", "openai-fast", "deepseek-flash"}


def test_only_the_three_routes_of_the_media_client_are_passed_through_with_auth(gateway):
    """model_list cannot hold voice and image models (no LiteLLM route for OpenRouter speech), so
    the gateway forwards exactly the routes media.py uses. Without auth: true anyone who reaches
    the gateway would spend the OpenRouter balance; include_subpath would open every other
    OpenRouter route to a holder of the gateway key."""
    endpoints = gateway["general_settings"]["pass_through_endpoints"]

    assert {e["path"] for e in endpoints} == {"/openrouter/audio/speech", "/openrouter/chat/completions", "/openrouter/images"}
    for e in endpoints:
        assert e["target"] == "https://openrouter.ai/api/v1" + e["path"].removeprefix("/openrouter")
        assert e["auth"] is True
        assert not e.get("include_subpath")
        assert e["headers"] == {"Authorization": "Bearer os.environ/OPENROUTER_API_KEY"}


def test_litellm_asks_openrouter_for_the_price_of_every_call():
    """The price the lecturer sees is OpenRouter's `usage.cost`, which LiteLLM requests by itself
    on every OpenRouter call; a version that stopped doing so would leave every price unknown."""
    from litellm.llms.openrouter.chat import transformation

    source = Path(transformation.__file__).read_text(encoding="utf-8")
    assert '"usage"' in source and '"include": True' in source


def test_the_backend_waits_out_the_gateways_worst_case(gateway):
    from app.llm.gateway import GATEWAY_TIMEOUT_SECONDS

    # The gateway owns retries and fallbacks; a client timeout shorter than its whole chain would cut it off
    # mid-chain and the abandoned upstream calls would still be billed.
    settings = gateway["router_settings"]
    longest_chain = 1 + max(len(backups) for chain in settings["fallbacks"] for backups in chain.values())
    worst = settings["timeout"] * (1 + settings["num_retries"]) * longest_chain
    assert worst < GATEWAY_TIMEOUT_SECONDS <= worst * 1.2
