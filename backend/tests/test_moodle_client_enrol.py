from app.moodle import client as moodle_client

# The conftest stands in for these in every test; these tests are about the real ones.
REAL_VERIFY_LOGIN = moodle_client.verify_login
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




def test_verify_login_sends_the_credentials_to_the_bridge_function_and_returns_the_user_id(monkeypatch):
    calls = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        return 55

    monkeypatch.setattr(moodle_client, "call", fake_call)

    assert REAL_VERIFY_LOGIN("lan.nguyen", "Aa1!secret-pass") == 55
    assert calls == [("local_lms_bridge_verify_login", {"username": "lan.nguyen", "password": "Aa1!secret-pass"})]


def test_verify_login_answers_none_when_moodle_says_the_credentials_are_wrong(monkeypatch):
    monkeypatch.setattr(moodle_client, "call", lambda wsfunction, **params: 0)

    assert REAL_VERIFY_LOGIN("lan.nguyen", "wrong") is None


def test_get_user_answers_the_lower_cased_email_and_the_full_name_and_none_for_a_user_moodle_left_out(monkeypatch):
    users = {
        42: [{"id": 42, "email": "Lan@Hcmut.EDU.vn", "firstname": "Nguyễn Thị", "lastname": " Lan "}],
        43: [],
        44: [{"id": 99, "email": "other@x.edu"}],  # Moodle answers with a neighbour: not this id
    }
    monkeypatch.setattr(moodle_client, "call", lambda wsfunction, **params: {"users": users[params["criteria"][0]["value"]]})

    assert moodle_client.get_user(42) == {"id": 42, "email": "lan@hcmut.edu.vn", "name": "Nguyễn Thị Lan"}
    assert moodle_client.get_user(43) is None
    assert moodle_client.get_user(44) is None
