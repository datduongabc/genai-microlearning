"""Moodle Web Services (REST) client (Tuần 11 -- mục 8 kiến trúc).

Vanilla Moodle web services cannot create a Book with chapters or a Quiz
with questions (`core_question_add_question` does not exist;
`mod_book`/`mod_quiz` only expose read-only WS functions). `create_book`
and `create_quiz` below call `local_lms_bridge_create_book` /
`local_lms_bridge_create_quiz` -- a small local plugin installed into the
Moodle instance (moodle-plugin/local_lms_bridge/) that adds exactly
those two functions, reusing Moodle's own production module/question-save
code paths rather than reimplementing them. `create_course` uses the real
core `core_course_create_courses` function directly -- no plugin needed
for that one.
"""

import uuid
from typing import Any

import httpx

from app.config import settings


class MoodleError(Exception):
    """Moodle returned an exception envelope instead of a normal result."""

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
    result = response.json()

    if isinstance(result, dict) and "exception" in result:
        raise MoodleError(
            f"{wsfunction}: {result.get('message', result['exception'])}", result.get("errorcode")
        )
    return result


def create_course(name: str) -> int:
    result = call(
        "core_course_create_courses",
        courses=[{
            "fullname": name,
            "shortname": f"{name[:80]}-{uuid.uuid4().hex[:8]}",
            "categoryid": 1,
        }],
    )
    return int(result[0]["id"])


# Moodle's own front page is course 1; it is not a course anyone teaches.
_SITE_COURSE_ID = 1

# Moodle's built-in "Teacher" (editingteacher) role -- id 3 on every fresh install.
_EDITING_TEACHER_ROLE_ID = 3


def find_users_by_email(emails: list[str]) -> dict[str, list[int]]:
    """The Moodle user ids behind each email, compared case-insensitively. Keys are the emails
    in lower case; an email nobody has maps to []. Moodle can hold several accounts with one
    email, so what comes back is a list."""
    wanted = list(dict.fromkeys(e.strip().lower() for e in emails))
    found: dict[str, list[int]] = {email: [] for email in wanted}
    if not wanted:
        return found
    for email in wanted:
        # core_user_get_users matches the email without regard to case (core_user_get_users_by_field
        # compares exactly, so it misses an account Moodle stores as "Lan@School.edu"). It treats _
        # and % as wildcards, hence the exact comparison below.
        result = call("core_user_get_users", criteria=[{"key": "email", "value": email}])
        for user in result.get("users", []):
            if str(user.get("email", "")).strip().lower() == email:
                found[email].append(int(user["id"]))
    return found


def user_exists(user_id: int) -> bool:
    """Whether Moodle still has this user; core_user_get_users leaves deleted ones out."""
    result = call("core_user_get_users", criteria=[{"key": "id", "value": user_id}])
    return any(int(user["id"]) == user_id for user in result.get("users", []))


def verify_login(username: str, password: str) -> int | None:
    """The Moodle user id when this username and password are right, None when they are not.
    Asks local_lms_bridge_verify_login, which only checks the password the way Moodle's own login
    form does (so Moodle's lockout counts a wrong attempt) and changes nothing about the user.
    The password is sent once and not kept or logged anywhere here."""
    user_id = int(call("local_lms_bridge_verify_login", username=username, password=password))
    return user_id or None


def enrol_as_teacher(course_id: int, user_id: int) -> None:
    """Enrol the Moodle user as the course's teacher.

    Moodle's own "add a course" form enrols its creator, but the
    core_course_create_courses web service does not, and the dashboard
    (/my/) lists only courses the logged-in user is enrolled in -- so a
    course created here would otherwise exist yet never show up for the
    teacher. `user_id` is the lecturer's own Moodle account (the one they linked with their
    Moodle username and password, see app/api/moodle_account.py), not the account that owns the
    web service token.
    """
    call(
        "enrol_manual_enrol_users",
        enrolments=[{"roleid": _EDITING_TEACHER_ROLE_ID, "userid": user_id, "courseid": course_id}],
    )


def list_courses() -> list[dict[str, Any]]:
    """Every course created in Moodle (not the site's own front page), for the drop-down a
    lecturer picks from when linking a course."""
    result = call("core_course_get_courses")
    return [
        {"id": c["id"], "fullname": c["fullname"], "shortname": c["shortname"]}
        for c in result
        if c["id"] != _SITE_COURSE_ID
    ]


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


def create_book(course_id: int, name: str, chapters: list[dict[str, Any]], section_num: int = 0) -> int:
    """Returns the created Book's course module id (cmid).

    Each item in `chapters` needs: title (str), content (HTML str), subchapter (int, 1 when the
    chapter sits under the previous top-level one, else 0). An int and not a bool: Moodle reads
    the form field as an integer and would refuse the text "True".
    """
    return int(call(
        "local_lms_bridge_create_book",
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
) -> int:
    """Returns the created quiz's course module id (cmid).

    Each item in `questions` needs: stem (str), choices (list[str]),
    correct_index (int), explanation (str, optional). `behaviour` is Moodle's
    preferredbehaviour: "deferredfeedback" (graded at the end, the Quiz) or
    "immediatefeedback" (feedback after each answer, the Practice).
    """
    return int(call(
        "local_lms_bridge_create_quiz",
        courseid=course_id,
        name=name,
        questions=questions,
        sectionnum=section_num,
        behaviour=behaviour,
    ))
