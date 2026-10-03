from app.moodle import client as moodle_client

# The conftest stands in for these in every test; these tests are about the real ones.
REAL_FIND_USERS_BY_EMAIL = moodle_client.find_users_by_email
REAL_ENROL_AS_TEACHER = moodle_client.enrol_as_teacher
REAL_USER_EXISTS = moodle_client.user_exists
EDITING_TEACHER_ROLE_ID = 3  # Moodle's built-in "Teacher" (editingteacher) role


def test_enrol_as_teacher_enrols_the_given_moodle_user_in_the_given_course(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))

    monkeypatch.setattr(moodle_client, "call", fake_call)

    REAL_ENROL_AS_TEACHER(course_id=7, user_id=42)

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
        REAL_ENROL_AS_TEACHER(course_id=7, user_id=42)
    except moodle_client.MoodleError:
        return
    raise AssertionError("MoodleError should propagate to the caller")


def test_find_users_by_email_asks_for_each_lower_cased_email_once_and_matches_exactly_without_case(monkeypatch):
    calls = []
    # Moodle's own search is case-insensitive but treats _ and % as wildcards: it answers with
    # neighbours too, and only an exact (case-folded) match may count.
    users = {
        "lan@example.com": [{"id": 11, "email": "Lan@Example.com"}, {"id": 99, "email": "lanx@example.com"}],
        "twin@example.com": [{"id": 12, "email": "twin@example.com"}, {"id": 13, "email": "TWIN@example.com"}],
        "none@example.com": [],
    }

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return {"users": users[params["criteria"][0]["value"]], "warnings": []}

    monkeypatch.setattr(moodle_client, "call", fake_call)

    found = REAL_FIND_USERS_BY_EMAIL(["LAN@example.com", "twin@example.com", "none@example.com", "lan@example.com"])

    assert calls == [
        ("core_user_get_users", {"criteria": [{"key": "email", "value": email}]})
        for email in ("lan@example.com", "twin@example.com", "none@example.com")
    ]
    assert found == {"lan@example.com": [11], "twin@example.com": [12, 13], "none@example.com": []}


def test_user_exists_looks_the_id_up_and_a_user_moodle_left_out_is_gone(monkeypatch):
    calls = []
    users = {42: [{"id": 42, "email": "lan@x.edu"}], 43: []}

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return {"users": users[params["criteria"][0]["value"]], "warnings": []}

    monkeypatch.setattr(moodle_client, "call", fake_call)

    assert REAL_USER_EXISTS(42) is True
    assert REAL_USER_EXISTS(43) is False
    assert calls[0] == ("core_user_get_users", {"criteria": [{"key": "id", "value": 42}]})


def test_find_users_by_email_with_no_emails_does_not_call_moodle(monkeypatch):
    monkeypatch.setattr(moodle_client, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))

    assert REAL_FIND_USERS_BY_EMAIL([]) == {}


def test_create_user_sends_the_email_as_username_and_returns_the_new_id(monkeypatch):
    calls = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return [{"id": 55, "username": "lan@example.com"}]

    monkeypatch.setattr(moodle_client, "call", fake_call)

    new_id = moodle_client.create_user("lan@example.com", "Lan", "Nguyen", "Aa1!secret-pass")

    assert new_id == 55
    assert calls == [(
        "core_user_create_users",
        {"users": [{
            "username": "lan@example.com", "password": "Aa1!secret-pass", "firstname": "Lan",
            "lastname": "Nguyen", "email": "lan@example.com", "auth": "manual",
        }]},
    )]
