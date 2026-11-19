"""Exporting a module as a SCORM 1.2 package: what an LMS reads from the manifest, what the learner
sees on the page, and the same rules as publishing to Moodle (owner only, no control module, no
question that failed the quality gate). No Moodle link is needed: that is what the package is for."""

import io
import json
import uuid
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import ComponentType, Course, Module, ModuleComponent, User
from tests.integration.test_moodle_publish_book import (
    EXPLANATION,
    QUIZ_STEM,
    QUIZ_STEM_2,
    Published,
    full_module,
)

client = TestClient(app)
NS = {"cp": "http://www.imsproject.org/xsd/imscp_rootv1p1p2", "adlcp": "http://www.adlnet.org/xsd/adlcp_rootv1p2"}


@pytest.fixture
def owner():
    p = Published()
    with SessionLocal() as db:
        # Not linked to Moodle: a package must not need it.
        db.get(Course, p.course_id).moodle_course_id = None
        db.commit()
    yield p
    p.cleanup()


def export(p: Published, module_id, headers=None):
    return client.get(f"/modules/{module_id}/scorm.zip", headers=headers or p.headers)


def unpack(response) -> tuple[ET.Element, BeautifulSoup, dict]:
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == ["imsmanifest.xml", "index.html"]
    manifest = ET.fromstring(archive.read("imsmanifest.xml"))
    page = BeautifulSoup(archive.read("index.html").decode(), "html.parser")
    data = json.loads(page.find("script", id="module-data").string)
    return manifest, page, data


def test_the_package_is_one_sco_an_lms_can_import(owner):
    response = export(owner, full_module(owner))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert unquote(response.headers["content-disposition"]) == "attachment; filename*=UTF-8''Encapsulation - SCORM.zip"
    manifest, _, data = unpack(response)
    assert manifest.findtext("cp:metadata/cp:schemaversion", namespaces=NS) == "1.2"
    resource = manifest.find("cp:resources/cp:resource", NS)
    assert resource.get("href") == "index.html"
    assert resource.get(f"{{{NS['adlcp']}}}scormtype") == "sco"
    item = manifest.find("cp:organizations/cp:organization/cp:item", NS)
    assert item.findtext("cp:title", namespaces=NS) == "Encapsulation"
    assert item.findtext("adlcp:masteryscore", namespaces=NS) == str(data["mastery"]) == "80"


def test_the_page_has_the_reading_then_the_practice_then_the_quiz(owner):
    _, page, data = unpack(export(owner, full_module(owner)))

    headings = [h.get_text() for h in page.select("main > section > h2")]
    assert headings == ["Mục tiêu học tập", "Nội dung", "Slide", "Khái niệm", "Lợi ích", "Ví dụ", "Luyện tập", "Kiểm tra"]
    # The content's own headings sit under its chapter title.
    assert page.find("h4", string="Định nghĩa") is not None
    text = page.get_text(" ")
    assert "phân tích Encapsulation" in text and "Số dư là private" in text
    assert "PRACTICE-STEM" in page.find(id="practice-0").get_text()
    assert QUIZ_STEM in page.find(id="quiz-0").get_text() and QUIZ_STEM_2 in page.find(id="quiz-1").get_text()
    assert len(page.find(id="quiz-0").find_all("input", type="radio")) == 4
    # The answer key and the explanations are not on the page until the learner answers.
    assert EXPLANATION not in text
    assert [q["correct"] for q in data["quiz"]] == [1, 1]
    # The teacher's edited explanation, as in the Moodle quiz feedback.
    assert EXPLANATION in data["quiz"][0]["explanation"]


def test_generated_text_cannot_add_a_script_to_the_package(owner):
    module_id = owner.module(
        learning_objective="LO",
        components=[
            (ComponentType.content, {"markdown": "<script>alert(2)</script> <img src=x onerror=alert(3)>"}, []),
            (ComponentType.quiz, {"questions": [{"passed": True}]},
             [("<script>alert(4)</script>stem", "</script><script>alert(5)</script>")]),
        ],
    )
    with SessionLocal() as db:
        db.get(Module, module_id).title = '</title><script>alert(1)</script>'
        db.commit()
    archive = zipfile.ZipFile(io.BytesIO(export(owner, module_id).content))
    page = archive.read("index.html").decode()

    soup = BeautifulSoup(page, "html.parser")
    scripts = soup.find_all("script")
    assert [s.get("id") for s in scripts] == ["module-data", None]
    assert "alert" not in scripts[1].string
    assert "</" not in scripts[0].string
    assert soup.find("img") is None and "onerror" not in page.split('<script type="application/json"')[0]
    assert "<script>" not in archive.read("imsmanifest.xml").decode()


def test_only_the_owner_can_export(owner):
    module_id = full_module(owner)
    with SessionLocal() as db:
        other = User(email=f"other-{uuid.uuid4()}@example.com", name="O", hashed_password="x", is_admin=False)
        db.add(other)
        db.commit()
        other_id = other.id
    try:
        response = export(owner, module_id, headers={"Authorization": f"Bearer {create_access_token(other_id)}"})
        assert response.status_code == 404
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == other_id).delete()
            db.commit()


def test_a_question_that_failed_the_quality_gate_blocks_the_export(owner):
    module_id = owner.module(
        learning_objective="LO",
        components=[(ComponentType.quiz, {"questions": [{"passed": False, "fail_reason": "hai đáp án đúng"}]},
                     [("stem", "explanation")])],
    )
    response = export(owner, module_id)
    assert response.status_code == 422
    # Named, not explained: the reason is under the question on the page, and a toast holds a line or two.
    assert response.json()["detail"] == (
        "Còn 1 câu hỏi cần xem lại (quiz 1). Lý do ghi dưới từng câu; hãy sửa trước khi xuất gói SCORM."
    )


def test_a_control_module_is_not_exported(owner):
    rag = full_module(owner)
    control = full_module(owner)
    with SessionLocal() as db:
        db.get(Module, control).paired_module_id = rag
        db.get(Module, control).generation_payload = {"use_retrieval": False}
        db.commit()
    assert export(owner, control).status_code == 422


def test_an_empty_module_is_not_exported(owner):
    module_id = owner.module(components=[(ComponentType.content, {"markdown": "  "}, [])])
    assert export(owner, module_id).status_code == 422


def test_a_module_without_questions_is_still_a_valid_package(owner):
    module_id = owner.module(learning_objective="LO", components=[(ComponentType.content, {"markdown": "Bài đọc"}, [])])
    _, page, data = unpack(export(owner, module_id))
    assert data["quiz"] == [] and page.find(id="quiz-submit") is None
    with SessionLocal() as db:
        assert db.query(ModuleComponent).filter(ModuleComponent.module_id == module_id).count() == 1
