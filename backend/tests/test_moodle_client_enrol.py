from app.moodle import client as moodle_client

# The conftest stands in for it in every test; these tests are about the real one.
REAL_FIND_USERS_BY_EMAIL = moodle_client.find_users_by_email
EDITING_TEACHER_ROLE_ID = 3  # Moodle's built-in "Teacher" (editingteacher) role


def test_enrol_as_teacher_enrols_the_given_moodle_user_in_the_given_course(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))

    monkeypatch.setattr(moodle_client, "call", fake_call)

    moodle_client.enrol_as_teacher(course_id=7, user_id=42)

    # Not the owner of the web service token: no call asks who that is.
    assert calls == [
        (
            "enrol_manual_enrol_users",
            {"enrolments": [{"roleid": EDITING_TEACHER_ROLE_ID, "userid": 42, "courseid": 7}]},
        )
    ]


def test_enrol_as_teacher_surfaces_moodle_errors(monkeypatch):
    def fake_call(wsfunction, **params):
        raise moodle_client.MoodleError("enrol_manual_enrol_users: instance disabled")

    monkeypatch.setattr(moodle_client, "call", fake_call)

    try:
        moodle_client.enrol_as_teacher(course_id=7, user_id=42)
    except moodle_client.MoodleError:
        return
    raise AssertionError("MoodleError should propagate to the caller")


def test_find_users_by_email_asks_for_the_lower_cased_emails_once_and_matches_case_insensitively(monkeypatch):
    calls = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return [
            {"id": 11, "email": "Lan@Example.com"},
            {"id": 12, "email": "twin@example.com"},
            {"id": 13, "email": "twin@example.com"},
            {"id": 14, "email": "stranger@example.com"},
        ]

    monkeypatch.setattr(moodle_client, "call", fake_call)

    found = REAL_FIND_USERS_BY_EMAIL(["LAN@example.com", "twin@example.com", "none@example.com", "lan@example.com"])

    assert calls == [(
        "core_user_get_users_by_field",
        {"field": "email", "values": ["lan@example.com", "twin@example.com", "none@example.com"]},
    )]
    assert found == {"lan@example.com": [11], "twin@example.com": [12, 13], "none@example.com": []}


def test_find_users_by_email_with_no_emails_does_not_call_moodle(monkeypatch):
    monkeypatch.setattr(moodle_client, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))

    assert REAL_FIND_USERS_BY_EMAIL([]) == {}
