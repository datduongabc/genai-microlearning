"""Everything the backend asks Moodle for is something Moodle's "LMS Bridge service" allows,
and a fresh Moodle sets that service up by itself.

The tests of the Moodle endpoints replace the Moodle client, so a function the client calls
but the service does not list only fails against a real Moodle. This reads both sides and
compares them. It also checks that a new Moodle (after the database volume is recreated,
say) gets web services switched on and the token the backend is configured with, instead
of somebody creating them by hand in the admin pages."""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT = REPO_ROOT / "backend" / "app" / "moodle" / "client.py"
PLUGIN = REPO_ROOT / "moodle-plugin" / "local_lms_bridge"
MOODLE_IMAGE = REPO_ROOT / "devops" / "moodle"
COMPOSE = REPO_ROOT / "devops" / "docker-compose.yml"


def _functions_the_client_calls() -> set[str]:
    return set(re.findall(r'call\(\s*"([a-z_0-9]+)"', CLIENT.read_text(encoding="utf-8")))


def _functions_the_service_allows() -> set[str]:
    services = (PLUGIN / "db" / "services.php").read_text(encoding="utf-8")
    listed = re.search(r"'functions'\s*=>\s*\[(.*?)\]", services, re.DOTALL).group(1)
    return set(re.findall(r"'([a-z_0-9]+)'", listed))


def test_the_client_calls_something_so_this_proves_anything():
    assert len(_functions_the_client_calls()) >= 7


def test_every_function_the_client_calls_is_allowed_by_the_bridge_service():
    not_allowed = _functions_the_client_calls() - _functions_the_service_allows()

    assert not_allowed == set()


def test_the_plugin_version_moves_when_the_service_changes():
    # Moodle only re-reads a plugin's service definition when its version goes up.
    version = re.search(r"\$plugin->version\s*=\s*(\d+)", (PLUGIN / "version.php").read_text(encoding="utf-8"))

    # 2026092002: create_book replaced create_page in the service.
    assert int(version.group(1)) >= 2026092002


def test_a_fresh_moodle_switches_web_services_on_and_gets_the_configured_token():
    entrypoint = (MOODLE_IMAGE / "docker-entrypoint.sh").read_text(encoding="utf-8")
    script = (MOODLE_IMAGE / "configure-webservice.php").read_text(encoding="utf-8")

    assert "configure-webservice.php" in entrypoint
    assert "enablewebservices" in script and "rest" in script
    assert "lms_bridge" in script
    assert 'getenv("MOODLE_TOKEN")' in script or "getenv('MOODLE_TOKEN')" in script
    assert "COPY devops/moodle/configure-webservice.php" in (MOODLE_IMAGE / "Dockerfile").read_text(encoding="utf-8")


def test_moodle_is_given_the_token_the_backend_uses():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]

    assert services["moodle"]["environment"]["MOODLE_TOKEN"] == "${MOODLE_TOKEN}"
    assert services["backend"]["environment"]["MOODLE_TOKEN"] == "${MOODLE_TOKEN}"
