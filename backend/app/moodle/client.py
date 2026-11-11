"""Moodle Web Services (REST) client (Tuần 11 -- mục 8 kiến trúc).

Vanilla Moodle web services cannot create a Book with chapters or a Quiz
with questions (`core_question_add_question` does not exist;
`mod_book`/`mod_quiz` only expose read-only WS functions). `create_book`
and `create_quiz` below call `local_lms_bridge_create_book` /
`local_lms_bridge_create_quiz` -- a small local plugin installed into the
Moodle instance (moodle-plugin/local_lms_bridge/). The plugin also handles
native Moodle account confirmation and current capability-scoped course lookup.
Accounts, courses and enrollment are managed in Moodle itself.
"""

from json import JSONDecodeError
from typing import Any

import httpx

from app.config import settings


class MoodleNotConfigured(Exception):
    """No Moodle address is set (MOODLE_BASE_URL empty), as on an AWS environment whose LMS reaches
    the tool by LTI only. Account links and publishing are then unavailable, not failing."""


def configured() -> bool:
    return bool(settings.moodle_base_url.strip())


class MoodleError(Exception):
    """Moodle returned an exception envelope or an invalid JSON response."""

    def __init__(self, message: str, errorcode: str | None = None):
        super().__init__(message)
        self.errorcode = errorcode


def _flatten(prefix: str, value: Any) -> list[tuple[str, str]]:
    """Encode a nested dict/list into Moodle's expected PHP-style
    `key[subkey][0]=value` form fields."""
    if isinstance(value, dict):
        pairs = []
        for k, v in value.items():
            pairs.extend(_flatten(f"{prefix}[{k}]", v))
        return pairs
    if isinstance(value, list):
        pairs = []
        for i, v in enumerate(value):
            pairs.extend(_flatten(f"{prefix}[{i}]", v))
        return pairs
    return [(prefix, "" if value is None else str(value))]


def call(wsfunction: str, **params: Any) -> Any:
    if not configured():
        # Without this the request went to "/webservice/rest/server.php", httpx refused a URL with
        # no scheme, and the lecturer read "could not reach Moodle, try again later".
        raise MoodleNotConfigured(wsfunction)
    form: dict[str, str] = {
        "wstoken": settings.moodle_token,
        "wsfunction": wsfunction,
        "moodlewsrestformat": "json",
    }
    for key, value in params.items():
        form.update(_flatten(key, value))

    url = f"{settings.moodle_base_url}/webservice/rest/server.php"
    # A Host header that doesn't match Moodle's configured $CFG->wwwroot
    # makes it 303-redirect every request there instead of answering it --
    # send the Host header Moodle expects while still connecting via
    # moodle_base_url (which, inside Docker, is host.docker.internal, not
    # wwwroot's localhost).
    wwwroot_host = httpx.URL(settings.moodle_wwwroot).netloc.decode()
    response = httpx.post(url, data=form, headers={"Host": wwwroot_host}, timeout=30.0)
    response.raise_for_status()
    try:
        result = response.json()
    except JSONDecodeError as error:
        raise MoodleError(f"{wsfunction}: Moodle returned an invalid JSON response") from error

    if isinstance(result, dict) and "exception" in result:
        raise MoodleError(
            f"{wsfunction}: {result.get('message', result['exception'])}", result.get("errorcode")
        )
    return result


def get_course(course_id: int) -> dict[str, Any] | None:
    """The Moodle course with this id, or None when there is none."""
    try:
        result = call("core_course_get_courses", options={"ids": [course_id]})
    except MoodleError as error:
        if error.errorcode == "invalidrecord":
            return None
        raise
    for course in result:
        if course["id"] == course_id:
            return {"id": course["id"], "fullname": course["fullname"], "shortname": course["shortname"]}
    return None


def get_course_sections(course_id: int) -> list[dict[str, Any]]:
    """List a linked Moodle course's existing sections, so a teacher can pick
    where to place newly-pushed Page/Quiz activities instead of everything
    always landing in section 0."""
    result = call("core_course_get_contents", courseid=course_id)
    return [{"sectionnum": s["section"], "name": s["name"]} for s in result]


def create_book(course_id: int, name: str, chapters: list[dict[str, Any]], section_num: int = 0, *, actor_user_id: int) -> int:
    """Returns the created Book's course module id (cmid).

    Each item in `chapters` needs: title (str), content (HTML str), subchapter (int, 1 when the
    chapter sits under the previous top-level one, else 0). An int and not a bool: Moodle reads
    the form field as an integer and would refuse the text "True".
    """
    return int(call(
        "local_lms_bridge_create_book",
        actorid=actor_user_id,
        courseid=course_id,
        name=name,
        chapters=chapters,
        sectionnum=section_num,
    ))


def create_quiz(
    course_id: int,
    name: str,
    questions: list[dict[str, Any]],
    section_num: int = 0,
    behaviour: str = "deferredfeedback",
    *, actor_user_id: int,
) -> int:
    """Returns the created quiz's course module id (cmid).

    Each item in `questions` needs: stem (str), choices (list[str]),
    correct_index (int), explanation (str, optional). `behaviour` is Moodle's
    preferredbehaviour: "deferredfeedback" (graded at the end, the Quiz) or
    "immediatefeedback" (feedback after each answer, the Practice).
    """
    return int(call(
        "local_lms_bridge_create_quiz",
        actorid=actor_user_id,
        courseid=course_id,
        name=name,
        questions=questions,
        sectionnum=section_num,
        behaviour=behaviour,
    ))


def request_account_link(*, app_account: str, email: str, app_email: str, app_name: str) -> dict:
    return call("local_lms_bridge_request_link", app_account=app_account, email=email,
                app_email=app_email, app_name=app_name)


def account_link_status(*, request_id: str, app_account: str) -> dict:
    return call("local_lms_bridge_link_status", request_id=request_id, app_account=app_account)


def teacher_courses(user_id: int) -> dict:
    return call("local_lms_bridge_teacher_courses", user_id=user_id)
