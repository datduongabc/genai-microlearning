"""The backend asks Moodle for a Book through the bridge plugin: a name and a list of chapters
in, the course module id of the new Book out. Nested chapters have to reach Moodle in the
PHP style form fields (chapters[0][title]) that its web service parses."""

import httpx
import pytest

from app.moodle import client as moodle_client

CHAPTERS = [
    {"title": "Mục tiêu học tập", "content": "<p>Goal</p>", "subchapter": 0},
    {"title": "Slide", "content": "<p>Outline</p>", "subchapter": 0},
    {"title": "Slide 1", "content": "<ul><li>a</li></ul>", "subchapter": 1},
]


def test_create_book_asks_the_bridge_for_a_book_and_returns_its_course_module_id(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return 77

    monkeypatch.setattr(moodle_client, "call", fake_call)

    cmid = moodle_client.create_book(6, "Encapsulation", CHAPTERS, section_num=2)

    assert cmid == 77
    assert calls == [
        (
            "local_lms_bridge_create_book",
            {"courseid": 6, "name": "Encapsulation", "chapters": CHAPTERS, "sectionnum": 2},
        )
    ]


def test_create_book_defaults_to_the_first_section(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(moodle_client, "call", lambda wsfunction, **params: calls.append(params) or 1)

    moodle_client.create_book(6, "B", CHAPTERS)

    assert calls[0]["sectionnum"] == 0


def test_the_chapters_are_sent_as_php_style_form_fields(monkeypatch):
    sent: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        sent.update(data)
        return httpx.Response(200, json=5, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    assert moodle_client.create_book(6, "B", CHAPTERS) == 5

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
        moodle_client.create_book(999, "B", CHAPTERS)
