"""Consistency of the gateway setup (devops/gateway, devops/docker-compose.yml) with
the backend. These files are never executed by the test suite, so a typo in a
model alias or an accidentally committed key would otherwise only show up as a
failed LLM call."""

import re
from pathlib import Path

import pytest
import yaml

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_CONFIG = REPO_ROOT / "devops" / "gateway" / "config.yaml"
GATEWAY_DOCKERFILE = REPO_ROOT / "devops" / "gateway" / "Dockerfile"
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"
BACKEND_LOCK = REPO_ROOT / "backend" / "uv.lock"

SUPPORTED_PROVIDERS = {"gemini", "openai", "anthropic"}
PROVIDER_KEY_VARIABLES = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(scope="module")
def gateway():
    return yaml.safe_load(GATEWAY_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _deployments(gateway) -> list[dict]:
    return gateway["model_list"]


def test_every_alias_the_backend_can_ask_for_is_defined_in_the_gateway(gateway):
    defaults = {Settings.model_fields[name].default for name in ("llm_model_dev", "llm_model_demo")}
    defined = {d["model_name"] for d in _deployments(gateway)}

    assert defaults <= defined


def test_only_the_supported_providers_are_routed(gateway):
    providers = {d["litellm_params"]["model"].split("/")[0] for d in _deployments(gateway)}

    assert providers <= SUPPORTED_PROVIDERS


def test_every_supported_provider_is_reachable_through_an_alias(gateway):
    providers = {d["litellm_params"]["model"].split("/")[0] for d in _deployments(gateway)}

    assert providers == SUPPORTED_PROVIDERS


def test_no_secret_is_written_in_the_gateway_config(gateway):
    keys = [d["litellm_params"]["api_key"] for d in _deployments(gateway) if "api_key" in d["litellm_params"]]
    keys.append(gateway["general_settings"]["master_key"])

    assert keys, "expected at least the master key to be configured"
    assert all(str(k).startswith("os.environ/") for k in keys)


def test_no_local_model_is_run_or_routed(gateway, compose):
    """Every model is called through a provider's API: nothing in the stack serves one
    itself, and no alias points at a host inside the compose network."""
    for service in compose["services"].values():
        assert "ollama" not in str(service.get("image", "")).lower()
    assert not {name for name in compose["services"] if "ollama" in name}
    assert not any("api_base" in d["litellm_params"] for d in _deployments(gateway))
    assert "local" not in {d["model_name"] for d in _deployments(gateway)}


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
