from app.services import moodle_client

EDITING_TEACHER_ROLE_ID = 3  # Moodle's built-in "Teacher" (editingteacher) role


def test_enrol_as_teacher_enrols_the_token_owner_in_the_given_course(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_call(wsfunction, **params):
        calls.append((wsfunction, params))
        if wsfunction == "core_webservice_get_site_info":
            return {"userid": 42, "username": "admin"}
        return None

    monkeypatch.setattr(moodle_client, "call", fake_call)

    moodle_client.enrol_as_teacher(course_id=7)

    enrol_calls = [params for name, params in calls if name == "enrol_manual_enrol_users"]
    assert enrol_calls == [
        {"enrolments": [{"roleid": EDITING_TEACHER_ROLE_ID, "userid": 42, "courseid": 7}]}
    ]


def test_enrol_as_teacher_surfaces_moodle_errors(monkeypatch):
    def fake_call(wsfunction, **params):
        if wsfunction == "core_webservice_get_site_info":
            return {"userid": 1}
        raise moodle_client.MoodleError("enrol_manual_enrol_users: instance disabled")

    monkeypatch.setattr(moodle_client, "call", fake_call)

    try:
        moodle_client.enrol_as_teacher(course_id=7)
    except moodle_client.MoodleError:
        return
    raise AssertionError("MoodleError should propagate to the caller")
