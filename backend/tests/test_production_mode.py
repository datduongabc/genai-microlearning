"""With ENVIRONMENT=production the service refuses to start on a configuration that would
leave authentication weak, does not publish its own API description, and every response
carries the security headers. Development stays as convenient as before."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app

GOOD = {
    "environment": "production",
    "secret_key": "k" * 64,
    "database_url": "postgresql+psycopg://svc_owner:Owner-Pass-93@db:5432/app",
    "app_runtime_database_url": "postgresql+psycopg://app_runtime:Runtime-Pass-71@db:5432/app",
    "app_worker_database_url": "postgresql+psycopg://app_worker:Worker-Pass-55@db:5432/app",
}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **overrides})


def test_a_sound_production_configuration_is_accepted():
    assert _settings().environment == "production"


@pytest.mark.parametrize("secret", ["", "k" * 31])
def test_production_refuses_a_missing_or_short_signing_key(secret):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _settings(secret_key=secret)


def test_production_accepts_a_signing_key_of_exactly_32_characters():
    assert _settings(secret_key="k" * 32).secret_key == "k" * 32


@pytest.mark.parametrize(
    "field, url",
    [
        ("database_url", "postgresql+psycopg://postgres:postgres@db:5432/app"),
        ("database_url", "postgresql+psycopg://svc_owner@db:5432/app"),
        ("app_runtime_database_url", "postgresql+psycopg://app_runtime:app_runtime_dev_password@db:5432/app"),
    ],
    ids=["superuser-default-password", "no-password", "runtime-default-password"],
)
def test_production_refuses_database_credentials_that_are_public_defaults(field, url):
    with pytest.raises(ValidationError, match="password"):
        _settings(**{field: url})


def test_production_refuses_serving_requests_with_the_same_role_or_password_as_the_owner():
    # Row level security only applies to a role that does not own the tables, and one
    # password shared by both roles is one leak away from bypassing it.
    same_role = "postgresql+psycopg://svc_owner:Runtime-Pass-71@db:5432/app"
    same_password = "postgresql+psycopg://app_runtime:Owner-Pass-93@db:5432/app"

    with pytest.raises(ValidationError, match="role"):
        _settings(app_runtime_database_url=same_role)
    with pytest.raises(ValidationError, match="password"):
        _settings(app_runtime_database_url=same_password)


def test_production_does_not_need_the_owner_credentials_at_all(monkeypatch):
    # The process that serves requests and runs the worker has no use for them: only
    # migrations do, and those run apart from it.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    without_owner = {key: value for key, value in GOOD.items() if key != "database_url"}

    assert Settings(_env_file=None, **without_owner).environment == "production"


@pytest.mark.parametrize(
    "url, problem",
    [
        ("postgresql+psycopg://app_worker:app_worker_dev_password@db:5432/app", "password"),
        ("postgresql+psycopg://app_worker@db:5432/app", "password"),
        ("postgresql+psycopg://app_runtime:Worker-Pass-55@db:5432/app", "role"),
        ("postgresql+psycopg://svc_owner:Worker-Pass-55@db:5432/app", "role"),
        ("postgresql+psycopg://app_worker:Runtime-Pass-71@db:5432/app", "password"),
        ("postgresql+psycopg://app_worker:Owner-Pass-93@db:5432/app", "password"),
    ],
    ids=["default-password", "no-password", "role-of-the-web-process", "role-of-the-owner",
         "password-of-the-web-process", "password-of-the-owner"],
)
def test_production_refuses_a_worker_that_is_not_a_role_and_password_of_its_own(url, problem):
    with pytest.raises(ValidationError, match=problem) as caught:
        _settings(app_worker_database_url=url)

    assert "APP_WORKER_DATABASE_URL" in str(caught.value)


def test_production_reports_every_problem_at_once():
    with pytest.raises(ValidationError) as caught:
        _settings(secret_key="", database_url="postgresql+psycopg://postgres:postgres@db:5432/app")

    message = str(caught.value)
    assert "SECRET_KEY" in message and "DATABASE_URL" in message


def test_development_keeps_working_without_any_of_that():
    settings = Settings(_env_file=None, secret_key="")

    assert settings.environment == "development"
    assert settings.secret_key  # an ephemeral key was generated


def test_an_unknown_environment_is_refused():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prod")


@pytest.fixture(scope="module")
def dev():
    return TestClient(create_app(Settings(_env_file=None, secret_key="s" * 40)))


@pytest.fixture(scope="module")
def prod():
    return TestClient(create_app(_settings()))


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_production_does_not_publish_the_api_description(dev, prod, path):
    assert dev.get(path).status_code == 200
    assert prod.get(path).status_code == 404


BASIC_HEADERS = {"x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "no-referrer"}


@pytest.mark.parametrize("client_name", ["dev", "prod"])
@pytest.mark.parametrize(
    "method, path, expected",
    [("get", "/health", 200), ("get", "/courses", 401), ("get", "/no-such-route", 404), ("post", "/auth/login", 422)],
)
def test_every_response_carries_the_basic_security_headers(request, client_name, method, path, expected):
    client = request.getfixturevalue(client_name)

    response = getattr(client, method)(path)

    assert response.status_code == expected
    for name, value in BASIC_HEADERS.items():
        assert response.headers[name] == value


def test_production_adds_transport_and_content_policies_that_development_leaves_out(dev, prod):
    hardened = prod.get("/health").headers

    assert "max-age=31536000" in hardened["strict-transport-security"]
    assert "default-src 'none'" in hardened["content-security-policy"]
    assert "frame-ancestors 'none'" in hardened["content-security-policy"]
    assert "strict-transport-security" not in dev.get("/health").headers


@pytest.mark.parametrize("client_name", ["dev", "prod"])
def test_responses_that_can_carry_tokens_are_never_cached(request, client_name):
    client = request.getfixturevalue(client_name)

    login = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever-1"})

    assert login.headers["cache-control"] == "no-store"
