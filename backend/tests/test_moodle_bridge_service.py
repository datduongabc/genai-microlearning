"""The Compose environment gives Moodle the same service token as the backend."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "devops" / "docker-compose.yml"


def test_moodle_is_given_the_token_the_backend_uses():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]

    assert services["moodle"]["environment"]["MOODLE_TOKEN"] == "${MOODLE_TOKEN}"
    assert services["backend"]["environment"]["MOODLE_TOKEN"] == "${MOODLE_TOKEN}"
