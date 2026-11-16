"""Reference files of a study: the admin uploads them and picks one per packet; an invited rater
downloads the one of the packet in front of them, and nobody else gets it. Local store, as in
test_upload_steps; the S3 links are covered in tests/unit/test_upload_store.py."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.db import SessionLocal
from app.jobs import maintenance
from app.models import StudyFile
from app.storage import study_file_key, study_file_store
from tests.integration.test_blind_ratings import blind as blind_study
from tests.integration.test_blind_ratings import client
from tests.integration.test_studies import world as study_world

world = study_world
blind = blind_study
BODY = b"%PDF-1.4 reference"


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_store", "local")
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))


def _upload(blind, filename="Bài giảng 3.pdf", body=BODY) -> dict:
    base = f"/studies/{blind['sid']}/files"
    started = client.post(base, headers=blind["headers"], json={"filename": filename, "size": len(body)})
    assert started.status_code == 201, started.text
    upload = started.json()["upload"]
    assert upload["method"] == "PUT"
    assert client.put(upload["url"], headers=blind["headers"], content=body).status_code == 200
    done = client.post(f"{base}/{started.json()['file_id']}/complete", headers=blind["headers"])
    assert done.status_code == 200, done.text
    return done.json()


def _admin_items(blind) -> list[dict]:
    return client.get(f"/studies/{blind['sid']}", headers=blind["headers"]).json()["items"]


def _set(blind, item_id, file_id):
    return client.put(f"/studies/{blind['sid']}/items/{item_id}/reference", headers=blind["headers"],
                      json={"file_id": file_id})


def _rater_get(blind, headers, item_id):
    path = f"/studies/mine/{blind['sid']}/items/{item_id}/reference"
    response = client.get(path, headers=headers)
    if response.status_code != 200:
        return response
    target = response.json()
    assert target["via_api"] is True and target["url"] == f"{path}/content"
    return client.get(target["url"], headers=headers)


def test_upload_lists_the_file_with_its_real_size(blind):
    file = _upload(blind)
    assert (file["filename"], file["size"]) == ("Bài giảng 3.pdf", len(BODY))
    assert client.get(f"/studies/{blind['sid']}/files", headers=blind["headers"]).json() == [file]
    download = client.get(f"/studies/{blind['sid']}/files/{file['id']}/download", headers=blind["headers"]).json()
    content = client.get(download["url"], headers=blind["headers"])
    assert content.content == BODY
    assert "filename*=utf-8''B%C3%A0i%20gi%E1%BA%A3ng%203.pdf" in content.headers["content-disposition"]


def test_a_reference_file_larger_than_an_api_body_still_uploads(blind):
    body = b"x" * (settings.max_request_body_bytes + 1)
    assert _upload(blind, body=body)["size"] == len(body)


def test_a_format_or_size_a_document_could_not_have_is_refused_before_transfer(blind):
    base = f"/studies/{blind['sid']}/files"
    assert client.post(base, headers=blind["headers"], json={"filename": "a.exe", "size": 1}).status_code == 422
    assert client.post(base, headers=blind["headers"], json={"filename": "a.pdf", "size": 26 * 2**20}).status_code == 413


def test_completing_before_the_file_arrived_keeps_the_request(blind):
    base = f"/studies/{blind['sid']}/files"
    started = client.post(base, headers=blind["headers"], json={"filename": "a.pdf", "size": 3}).json()
    assert client.post(f"{base}/{started['file_id']}/complete", headers=blind["headers"]).status_code == 409
    assert client.get(base, headers=blind["headers"]).json() == []


def test_a_pair_shares_its_file_and_a_human_packet_has_its_own(blind):
    file = _upload(blind)
    items = _admin_items(blind)
    rag = next(i for i in items if i["group"] == "rag_llm")
    control = next(i for i in items if i["group"] == "llm_no_rag")
    humans = [i for i in items if i["group"] == "human"]

    response = _set(blind, control["id"], file["id"])

    assert response.status_code == 200, response.text
    refs = {row["id"]: row["reference_file_id"] for row in response.json()}
    assert refs[rag["id"]] == refs[control["id"]] == file["id"]
    assert all(refs[h["id"]] is None for h in humans)
    assert {r["id"]: r["reference_file_id"] for r in _set(blind, humans[0]["id"], file["id"]).json()}[humans[0]["id"]] == file["id"]
    assert all(r["reference_file_id"] is None for r in _set(blind, rag["id"], None).json() if r["id"] in (rag["id"], control["id"]))


def test_an_invited_rater_downloads_the_file_of_the_packet(blind):
    file = _upload(blind)
    rag = next(i for i in _admin_items(blind) if i["group"] == "rag_llm")
    _set(blind, rag["id"], file["id"])

    items = client.get(f"/studies/mine/{blind['sid']}/items", headers=blind["second_headers"]).json()["items"]
    with_file = [i for i in items if i["reference"]]
    assert len(with_file) == 2
    assert all(i["reference"] == {"filename": "Bài giảng 3.pdf", "size": len(BODY)} for i in with_file)
    assert _rater_get(blind, blind["second_headers"], with_file[0]["id"]).content == BODY
    without = next(i for i in items if not i["reference"])
    assert _rater_get(blind, blind["second_headers"], without["id"]).status_code == 404


def test_someone_not_invited_gets_nothing(blind):
    file = _upload(blind)
    rag = next(i for i in _admin_items(blind) if i["group"] == "rag_llm")
    _set(blind, rag["id"], file["id"])
    assert _rater_get(blind, blind["outsider_headers"], rag["id"]).status_code == 404
    base = f"/studies/{blind['sid']}/files"
    for headers in (blind["teacher_headers"], blind["outsider_headers"]):
        assert client.get(base, headers=headers).status_code == 404
        assert client.get(f"{base}/{file['id']}/download", headers=headers).status_code == 404
        assert client.post(base, headers=headers, json={"filename": "a.pdf", "size": 1}).status_code == 404
        assert _set({**blind, "headers": headers}, rag["id"], None).status_code == 404


def test_a_file_from_another_study_cannot_be_picked(blind, world):
    other = client.post("/studies", headers=world["headers"], json={"name": "Other", "rubric_version": "v1"}).json()["id"]
    world["studies"].append(uuid.UUID(other))
    file = _upload({**blind, "sid": other})
    item = _admin_items(blind)[0]
    assert _set(blind, item["id"], file["id"]).status_code == 404


def test_deleting_a_file_clears_the_packets_and_the_store(blind):
    file = _upload(blind)
    item = _admin_items(blind)[0]
    _set(blind, item["id"], file["id"])
    key = study_file_key(file["id"], file["filename"])
    assert study_file_store().size(key) == len(BODY)

    assert client.delete(f"/studies/{blind['sid']}/files/{file['id']}", headers=blind["headers"]).status_code == 200

    assert study_file_store().size(key) is None
    assert all(i["reference_file_id"] is None for i in _admin_items(blind))


def test_maintenance_removes_an_upload_that_never_finished(blind):
    base = f"/studies/{blind['sid']}/files"
    stale = client.post(base, headers=blind["headers"], json={"filename": "old.pdf", "size": 3}).json()["file_id"]
    fresh = client.post(base, headers=blind["headers"], json={"filename": "new.pdf", "size": 3}).json()["file_id"]
    with SessionLocal() as db:
        db.get(StudyFile, uuid.UUID(stale)).created_at = datetime.now(UTC) - timedelta(hours=2)
        db.commit()

    maintenance.purge_old_records()

    with SessionLocal() as db:
        assert db.get(StudyFile, uuid.UUID(stale)) is None
        assert db.get(StudyFile, uuid.UUID(fresh)) is not None
