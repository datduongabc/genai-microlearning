"""The admin's account management: who can use it, what it does to a lecturer's access, and that
it is logged. The admin flag and locked state are read from the database on every request, so a change
takes effect with the token the person already holds."""

import logging
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.security import decode_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, RefreshToken, User
from tests.auth_helpers import STRONG_PASSWORD, create_and_login, make_account

client = TestClient(app)


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.edu"


def _bearer(login: dict) -> dict:
    return {"Authorization": f"Bearer {login['access_token']}"}


@pytest.fixture
def admin():
    return create_and_login(client, _email("admin"), is_admin=True)


@pytest.fixture
def lecturer():
    return create_and_login(client, _email("lecturer"))


def _login(email, password=STRONG_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_the_list_shows_every_account_with_its_state(admin):
    email = _email("listed")
    make_account(email)

    users = {u["email"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[email]["is_admin"] is False and users[email]["is_active"] is True
    assert "hashed_password" not in users[email]
    assert any(u["is_admin"] is True for u in users.values())


def test_the_list_says_which_lecturers_have_a_stored_moodle_link(admin):
    linked, unlinked = _email("linked"), _email("unlinked")
    make_account(linked)
    make_account(unlinked)
    with SessionLocal() as db:
        db.query(User).filter(User.email == linked).update({"moodle_user_id": 10_000 + uuid.uuid4().int % 10**8})
        db.commit()

    users = {u["email"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    # The stored link, not whether Moodle happens to have a user with the same email: a lecturer
    # whose Moodle email differs from the app's is still linked.
    assert users[linked]["moodle_status"] == "linked"
    assert users[unlinked]["moodle_status"] == "not_linked"
    assert all(u["moodle_status"] is None for u in users.values() if u["is_admin"] is True)


def test_the_counts_cover_every_account_whatever_the_list_filter(admin):
    before = client.get("/admin/counts", headers=_bearer(admin)).json()
    make_account(_email("active"))
    disabled = make_account(_email("disabled"))
    assert client.post(f"/admin/users/{disabled.id}/disable", headers=_bearer(admin)).status_code == 200

    after = client.get("/admin/counts", headers=_bearer(admin)).json()

    assert after["users"] - before["users"] == 2
    assert after["inactive_users"] - before["inactive_users"] == 1
    listed = client.get("/admin/users", params={"is_active": False}, headers=_bearer(admin)).json()
    assert after["inactive_users"] == len(listed)


def test_a_disabled_lecturer_is_locked_out_at_once_including_the_token_they_hold(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    email = lecturer["user"]["email"]
    assert client.get("/courses", headers=_bearer(lecturer)).status_code == 200

    disabled = client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))

    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    assert client.get("/courses", headers=_bearer(lecturer)).status_code == 401
    refresh = client.post("/auth/refresh", json={"refresh_token": lecturer["refresh_token"]})
    assert refresh.status_code == 401
    # Correct local credentials do not get a locked account in.
    login = _login(lecturer["user"]["email"])
    assert login.status_code == 403
    assert "quản trị viên" in login.json()["detail"]
    # Only said after the password is checked: a stranger learns nothing about the account.
    assert client.post("/auth/login", json={"email": email, "password": "not the password 1"}).status_code == 401




def test_an_admin_cannot_disable_themselves(admin):
    response = client.post(f"/admin/users/{admin['user']['id']}/disable", headers=_bearer(admin))

    assert response.status_code == 409
    assert client.get("/admin/users", headers=_bearer(admin)).status_code == 200








def test_an_unknown_user_is_not_found(admin):
    ghost = uuid.uuid4()

    for action in ("disable", "enable"):
        assert client.post(f"/admin/users/{ghost}/{action}", headers=_bearer(admin)).status_code == 404
    assert client.post(f"/admin/users/{ghost}/password", json={"password": STRONG_PASSWORD}, headers=_bearer(admin)).status_code in (404, 405)


def test_a_lecturer_gets_not_found_from_every_admin_route(lecturer):
    other = str(uuid.uuid4())
    calls = [
        client.get("/admin/users", headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/disable", headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/enable", headers=_bearer(lecturer)),
        client.get("/admin/jobs/failed", headers=_bearer(lecturer)),
        client.get("/admin/counts", headers=_bearer(lecturer)),
        client.post("/admin/jobs/retry", json={"job_ids": [other]}, headers=_bearer(lecturer)),
    ]

    assert [c.status_code for c in calls] == [404] * len(calls)
    assert len({c.text for c in calls}) == 1  # the same answer for all, nothing about what is there


def test_the_role_is_read_from_the_database_not_from_the_token(admin):
    claims = decode_token(admin["access_token"])
    assert not {"role", "is_admin", "roles"} & set(claims)
    assert client.get("/admin/users", headers=_bearer(admin)).status_code == 200

    with SessionLocal() as db:
        db.query(User).filter(User.id == uuid.UUID(admin["user"]["id"])).update({"is_admin": False})
        db.commit()

    assert client.get("/admin/users", headers=_bearer(admin)).status_code == 404


def test_the_token_of_an_account_that_was_never_active_or_is_forged_is_refused():
    forged = jwt.encode({"sub": str(uuid.uuid4())}, "not-the-secret-but-long-enough-for-hs256", algorithm="HS256")

    assert client.get("/admin/users", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_every_admin_action_is_logged_without_a_secret(admin, lecturer, caplog):
    caplog.set_level(logging.INFO)
    lecturer_id = lecturer["user"]["id"]
    client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))
    client.post(f"/admin/users/{lecturer_id}/enable", headers=_bearer(admin))

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]

    for event in ("admin_disable_user", "admin_enable_user"):
        assert any(
            event in line and f"actor_id={admin['user']['id']}" in line and lecturer_id in line for line in lines
        ), event
    everything = " ".join(r.getMessage() for r in caplog.records)
    assert STRONG_PASSWORD not in everything


def test_disabling_ends_every_login_of_the_account(admin, lecturer):
    client.post(f"/admin/users/{lecturer['user']['id']}/disable", headers=_bearer(admin))

    with SessionLocal() as db:
        live = db.query(RefreshToken).filter(
            RefreshToken.user_id == uuid.UUID(lecturer["user"]["id"]), RefreshToken.revoked_at.is_(None)
        ).count()

    assert live == 0




def _give_course(owner_id):
    with SessionLocal() as db:
        course = Course(name="Môn của giảng viên", owner_id=uuid.UUID(str(owner_id)))
        db.add(course)
        db.commit()
        return course.id




def test_the_list_says_how_many_courses_each_lecturer_has(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    empty = make_account(_email("empty"))
    _give_course(lecturer_id)
    _give_course(lecturer_id)

    users = {u["id"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[lecturer_id]["course_count"] == 2
    assert users[str(empty.id)]["course_count"] == 0
    assert users[admin["user"]["id"]]["course_count"] is None


def test_erasing_an_account_removes_who_they_were_and_what_they_said(admin, lecturer):
    """At the person's request (Decree 13/2023): name, email, password, chats, memberships and logins go;
    a study's rater list forgets them; the row stays, so a rating they gave keeps its rater."""
    from app.models import ChatSession, CourseMember, Study
    learner = create_and_login(client, _email("learner"))
    learner_id = uuid.UUID(learner["user"]["id"])
    with SessionLocal() as db:
        course = Course(name="Erase course", owner_id=uuid.UUID(lecturer["user"]["id"]))
        db.add(course)
        db.flush()
        db.add_all([CourseMember(course_id=course.id, user_id=learner_id),
                    ChatSession(course_id=course.id, user_id=learner_id, title="Hỏi")])
        email = db.get(User, learner_id).email.lower()
        study = Study(name="Erase study", rubric_version="v1", rater_emails=[email, "other@example.edu"], rater_pairs={email: ["p1"]})
        db.add(study)
        db.commit()
        course_id, study_id = course.id, study.id

    erased = client.post(f"/admin/users/{learner_id}/erase", headers=_bearer(admin))

    assert erased.status_code == 200
    with SessionLocal() as db:
        user = db.get(User, learner_id)
        assert (user.name, user.is_active, user.email) == ("Tài khoản đã xóa", False, f"erased-{learner_id}@invalid")
        assert db.query(ChatSession).filter_by(user_id=learner_id).count() == 0
        assert db.query(CourseMember).filter_by(user_id=learner_id).count() == 0
        assert db.query(RefreshToken).filter_by(user_id=learner_id).count() == 0
        study = db.get(Study, study_id)
        assert (study.rater_emails, study.rater_pairs) == (["other@example.edu"], {})
        db.delete(study)
        db.query(Course).filter_by(id=course_id).delete()
        db.commit()
    assert _login(email).status_code == 401


def test_an_account_that_owns_courses_is_not_erased_and_a_lecturer_cannot_erase(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    with SessionLocal() as db:
        course = Course(name="Kept", owner_id=uuid.UUID(lecturer_id))
        db.add(course)
        db.commit()
        course_id = course.id

    assert client.post(f"/admin/users/{lecturer_id}/erase", headers=_bearer(admin)).status_code == 409
    assert client.post(f"/admin/users/{lecturer_id}/erase", headers=_bearer(lecturer)).status_code == 404
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(lecturer_id)).is_active
        db.query(Course).filter_by(id=course_id).delete()
        db.commit()


def test_a_course_handed_over_keeps_its_material_and_lets_the_previous_lecturer_be_erased(admin, lecturer):
    """A lecturer leaves: the admin hands the course to another, who owns everything under it; the previous
    one stays a teacher if asked, and with no course of their own their data can then be erased."""
    from app.models import Document, DocumentStatus
    previous_id = uuid.UUID(lecturer["user"]["id"])
    successor = create_and_login(client, _email("successor"))
    with SessionLocal() as db:
        course = Course(name="Handed over", owner_id=previous_id)
        db.add(course)
        db.flush()
        db.add(Document(course_id=course.id, filename="notes.pdf", status=DocumentStatus.embedded))
        db.commit()
        course_id = str(course.id)

    listed = client.get(f"/admin/users/{previous_id}/courses", headers=_bearer(admin)).json()
    assert [c["id"] for c in listed] == [course_id]
    moved = client.post(f"/admin/courses/{course_id}/transfer", headers=_bearer(admin),
                        json={"new_owner_id": successor["user"]["id"], "keep_previous_as_teacher": True})

    assert moved.status_code == 200
    assert [c["id"] for c in client.get("/courses", headers=_bearer(successor)).json()] == [course_id]
    documents = client.get("/documents", params={"course_id": course_id}, headers=_bearer(successor)).json()
    assert [d["filename"] for d in documents] == ["notes.pdf"]
    # The previous lecturer still teaches it, without owning it.
    assert client.get("/documents", params={"course_id": course_id}, headers=_bearer(lecturer)).status_code == 200
    assert client.delete(f"/courses/{course_id}", headers=_bearer(lecturer)).status_code == 403
    assert client.get(f"/admin/users/{previous_id}/courses", headers=_bearer(admin)).json() == []
    assert client.post(f"/admin/users/{previous_id}/erase", headers=_bearer(admin)).status_code == 200
    with SessionLocal() as db:
        db.query(Course).filter_by(id=uuid.UUID(course_id)).delete()
        db.commit()


def test_a_course_is_not_handed_to_someone_who_cannot_teach_it_nor_by_a_lecturer(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    disabled = create_and_login(client, _email("disabled"))
    with SessionLocal() as db:
        db.get(User, uuid.UUID(disabled["user"]["id"])).is_active = False
        course = Course(name="Stays", owner_id=uuid.UUID(lecturer_id))
        db.add(course)
        db.commit()
        course_id = course.id

    def transfer(to, who=admin):
        return client.post(f"/admin/courses/{course_id}/transfer", headers=_bearer(who), json={"new_owner_id": to})

    assert transfer(disabled["user"]["id"]).status_code == 422
    assert transfer(lecturer_id).status_code == 409
    assert client.post(f"/admin/courses/{uuid.uuid4()}/transfer", headers=_bearer(admin),
                       json={"new_owner_id": lecturer_id}).status_code == 404
    assert transfer(admin["user"]["id"], who=lecturer).status_code == 404
    with SessionLocal() as db:
        assert db.get(Course, course_id).owner_id == uuid.UUID(lecturer_id)
        db.delete(db.get(Course, course_id))
        db.commit()
