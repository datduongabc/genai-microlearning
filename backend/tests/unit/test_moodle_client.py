"""Unit tests for the Moodle web-service client: error handling, course lookup, book & quiz payloads."""

import httpx
import pytest

from app.config import settings
from app.moodle import client as moodle_client
from app.moodle.errors import MOODLE_FAILURES, MOODLE_NOT_CONFIGURED, moodle_failure

# conftest thay get_course bằng bản luôn trả "có khoá học" cho mọi test không nói về nó; test ở
# đây nói về chính nó, nên giữ bản thật từ lúc nạp module.
REAL_GET_COURSE = moodle_client.get_course

CHAPTERS = [
    {"title": "Mục tiêu học tập", "content": "<p>Goal</p>", "subchapter": 0},
    {"title": "Slide", "content": "<p>Outline</p>", "subchapter": 0},
    {"title": "Slide 1", "content": "<ul><li>a</li></ul>", "subchapter": 1},
]


def _answer(monkeypatch, payload):
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None):
        sent.append(data)
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(moodle_client.httpx, "post", fake_post)
    return sent


def test_no_address_means_not_configured_and_no_request(monkeypatch):
    monkeypatch.setattr(settings, "moodle_base_url", "  ")
    monkeypatch.setattr(moodle_client.httpx, "post", lambda *a, **kw: pytest.fail("no request"))
    with pytest.raises(moodle_client.MoodleNotConfigured):
        moodle_client.call("core_webservice_get_site_info")


def test_not_configured_is_a_503_with_its_own_message():
    error = moodle_failure(moodle_client.MoodleNotConfigured("core_course_get_courses"), db=None)
    assert (error.status_code, error.detail) == (503, MOODLE_NOT_CONFIGURED)


def test_an_exception_in_a_successful_http_answer_is_raised_as_a_moodle_error(monkeypatch):
    _answer(monkeypatch, {"exception": "moodle_exception", "message": "Access control exception"})

    with pytest.raises(moodle_client.MoodleError, match="Access control exception"):
        moodle_client.call("core_course_get_courses")


@pytest.mark.parametrize("body", ["<html>upstream unavailable</html>", "", "{invalid json"])
def test_non_json_answer_is_a_controlled_moodle_failure(monkeypatch, body):
    monkeypatch.setattr(
        moodle_client.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(200, text=body, request=httpx.Request("POST", url)),
    )
    with pytest.raises(MOODLE_FAILURES):
        moodle_client.call("core_course_get_courses")


def test_get_course_is_none_when_moodle_has_no_course_with_that_id(monkeypatch):
    _answer(monkeypatch, [{"id": 1, "fullname": "Site", "shortname": "site"}])

    assert REAL_GET_COURSE(99) is None


def test_get_course_returns_the_matching_course(monkeypatch):
    _answer(monkeypatch, [{"id": 7, "fullname": "Cấu trúc dữ liệu", "shortname": "CO2003", "extra": 1}])

    assert REAL_GET_COURSE(7) == {"id": 7, "fullname": "Cấu trúc dữ liệu", "shortname": "CO2003"}


def test_create_quiz_defaults_to_deferred_feedback_for_old_callers(monkeypatch):
    sent = _answer(monkeypatch, 5)

    moodle_client.create_quiz(3, "Quiz", [], actor_user_id=9)

    assert sent[0]["behaviour"] == "deferredfeedback"


def test_create_quiz_sends_the_requested_behaviour_to_the_web_service(monkeypatch):
    sent = _answer(monkeypatch, 5)

    moodle_client.create_quiz(3, "Practice", [], behaviour="immediatefeedback", actor_user_id=9)

    assert sent[0]["wsfunction"] == "local_lms_bridge_create_quiz"
    assert sent[0]["behaviour"] == "immediatefeedback"


def test_create_book_asks_the_bridge_for_a_book_and_returns_its_course_module_id(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return 77

    monkeypatch.setattr(moodle_client, "call", fake_call)

    cmid = moodle_client.create_book(6, "Encapsulation", CHAPTERS, section_num=2, actor_user_id=9)

    assert cmid == 77
    assert calls == [
        (
            "local_lms_bridge_create_book",
            {"actorid": 9, "courseid": 6, "name": "Encapsulation", "chapters": CHAPTERS, "sectionnum": 2},
        )
    ]


def test_create_book_defaults_to_the_first_section(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(moodle_client, "call", lambda wsfunction, **params: calls.append(params) or 1)

    moodle_client.create_book(6, "B", CHAPTERS, actor_user_id=9)

    assert calls[0]["sectionnum"] == 0


def test_the_chapters_are_sent_as_php_style_form_fields(monkeypatch):
    sent: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        sent.update(data)
        return httpx.Response(200, json=5, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    assert moodle_client.create_book(6, "B", CHAPTERS, actor_user_id=9) == 5

    assert sent["wsfunction"] == "local_lms_bridge_create_book"
    assert sent["chapters[0][title]"] == "Mục tiêu học tập"
    assert sent["chapters[2][title]"] == "Slide 1"
    assert sent["chapters[2][subchapter]"] == "1"
    assert sent["chapters[1][subchapter]"] == "0"
    assert sent["chapters[2][content]"] == "<ul><li>a</li></ul>"


def test_a_moodle_error_reaches_the_caller(monkeypatch):
    def fake_call(wsfunction, **params):
        raise moodle_client.MoodleError("local_lms_bridge_create_book: no such course")

    monkeypatch.setattr(moodle_client, "call", fake_call)

    with pytest.raises(moodle_client.MoodleError):
        moodle_client.create_book(999, "B", CHAPTERS, actor_user_id=9)
