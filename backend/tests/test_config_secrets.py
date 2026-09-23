"""A failing assertion, a debug print or a log line that includes the settings
object must not spill credentials: pytest itself prints the repr of the objects
involved in a failed assertion."""

from app.config import Settings

SECRETS = {
    "database_url": "postgresql+psycopg://user:sentinel-db-password@localhost/db",
    "app_runtime_database_url": "postgresql+psycopg://app:sentinel-runtime-password@localhost/db",
    "secret_key": "sentinel-signing-secret",
    "moodle_token": "sentinel-moodle-token",
    "llm_gateway_key": "sentinel-gateway-key",
}


def test_secret_values_never_appear_in_the_settings_repr():
    text = repr(Settings(**SECRETS)) + str(Settings(**SECRETS))

    leaked = [name for name, value in SECRETS.items() if value in text or value.rsplit(":", 1)[-1] in text]
    assert leaked == []


def test_non_secret_settings_stay_visible_for_debugging():
    text = repr(Settings(**SECRETS, upload_storage_dir="somewhere"))

    assert "upload_storage_dir='somewhere'" in text
