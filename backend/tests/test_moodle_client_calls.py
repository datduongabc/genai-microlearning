"""Moodle trả lỗi của web service bằng HTTP 200 kèm trường "exception" trong JSON: không bắt ở
đây thì một lần tạo khoá học hay đẩy bài hỏng vẫn bị coi là thành công."""

import httpx
import pytest

from app.moodle import client as moodle_client

# conftest thay get_course bằng bản luôn trả "có khoá học" cho mọi test không nói về nó; test ở
# đây nói về chính nó, nên giữ bản thật từ lúc nạp module.
REAL_GET_COURSE = moodle_client.get_course


def _answer(monkeypatch, payload):
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None):
        sent.append(data)
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(moodle_client.httpx, "post", fake_post)
    return sent


def test_an_exception_in_a_successful_http_answer_is_raised_as_a_moodle_error(monkeypatch):
    _answer(monkeypatch, {"exception": "moodle_exception", "message": "Access control exception"})

    with pytest.raises(moodle_client.MoodleError, match="Access control exception"):
        moodle_client.call("core_course_get_courses")


def test_get_course_is_none_when_moodle_has_no_course_with_that_id(monkeypatch):
    _answer(monkeypatch, [{"id": 1, "fullname": "Site", "shortname": "site"}])

    assert REAL_GET_COURSE(99) is None


def test_get_course_returns_the_matching_course(monkeypatch):
    _answer(monkeypatch, [{"id": 7, "fullname": "Cấu trúc dữ liệu", "shortname": "CO2003", "extra": 1}])

    assert REAL_GET_COURSE(7) == {"id": 7, "fullname": "Cấu trúc dữ liệu", "shortname": "CO2003"}


def test_create_quiz_defaults_to_deferred_feedback_for_old_callers(monkeypatch):
    sent = _answer(monkeypatch, 5)

    moodle_client.create_quiz(3, "Quiz", [])

    assert sent[0]["behaviour"] == "deferredfeedback"


def test_create_quiz_sends_the_requested_behaviour_to_the_web_service(monkeypatch):
    sent = _answer(monkeypatch, 5)

    moodle_client.create_quiz(3, "Practice", [], behaviour="immediatefeedback")

    assert sent[0]["wsfunction"] == "local_lms_bridge_create_quiz"
    assert sent[0]["behaviour"] == "immediatefeedback"

