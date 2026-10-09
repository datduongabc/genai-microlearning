"""Moodle trả lỗi của web service bằng HTTP 200 kèm trường "exception" trong JSON: không bắt ở
đây thì một lần tạo khoá học hay đẩy bài hỏng vẫn bị coi là thành công."""

import httpx
import pytest

from app.moodle import client as moodle_client

# conftest thay get_course bằng bản luôn trả "có khoá học" cho mọi test không nói về nó; test ở
# đây nói về chính nó, nên giữ bản thật từ lúc nạp module.
REAL_GET_COURSE = moodle_client.get_course
REAL_TEACHER_COURSES = moodle_client.teacher_courses


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


@pytest.mark.parametrize("body", ["<html>upstream unavailable</html>", "", "{invalid json"])
def test_non_json_answer_is_a_controlled_moodle_failure(monkeypatch, body):
    from app.moodle.errors import MOODLE_FAILURES

    monkeypatch.setattr(moodle_client.httpx, "post", lambda url, **kwargs:
                        httpx.Response(200, text=body, request=httpx.Request("POST", url)))
    with pytest.raises(MOODLE_FAILURES):
        moodle_client.call("core_course_get_courses")


def test_html_moodle_response_returns_502_from_the_api(monkeypatch):
    import uuid

    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import User
    from tests.auth_helpers import create_and_login

    client = TestClient(app)
    login = create_and_login(client, f"moodle-html-{uuid.uuid4().hex}@example.com")
    with SessionLocal() as db:
        db.get(User, uuid.UUID(login["user"]["id"])).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.commit()
    monkeypatch.setattr(moodle_client, "teacher_courses", REAL_TEACHER_COURSES)
    monkeypatch.setattr(moodle_client.httpx, "post", lambda url, **kwargs:
                        httpx.Response(200, text="<html>unavailable</html>",
                                       request=httpx.Request("POST", url)))
    response = client.get("/courses/moodle/courses",
                          headers={"Authorization": "Bearer " + login["access_token"]})
    assert response.status_code == 502
    assert response.json() == {"detail": "Thao tác với Moodle thất bại, vui lòng thử lại sau."}


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
