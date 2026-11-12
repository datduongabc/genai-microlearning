import pytest

from app.config import settings
from app.moodle import client
from app.moodle.errors import MOODLE_NOT_CONFIGURED, moodle_failure


def test_no_address_means_not_configured_and_no_request(monkeypatch):
    monkeypatch.setattr(settings, 'moodle_base_url', '  ')
    monkeypatch.setattr(client.httpx, 'post', lambda *a, **kw: pytest.fail('no request'))
    with pytest.raises(client.MoodleNotConfigured):
        client.call('core_webservice_get_site_info')


def test_not_configured_is_a_503_with_its_own_message():
    error = moodle_failure(client.MoodleNotConfigured('core_course_get_courses'), db=None)
    assert (error.status_code, error.detail) == (503, MOODLE_NOT_CONFIGURED)
