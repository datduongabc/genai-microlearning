"""The upload stores: the API and the worker run as separate ECS tasks, so the file must reach the
worker through S3, and the local store must keep working for compose and older queued jobs."""

import os
from pathlib import Path

import pytest

from app import storage
from app.config import Settings, settings
from app.storage import LocalUploadStore, S3UploadStore, upload_key, uploaded_file


class FakeS3:
    """Stands in for boto3's S3 client: the three calls the store makes, kept in a dict."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def upload_file(self, filename, bucket, key):
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_store_moves_the_file_up_and_hands_out_a_temporary_copy(tmp_path):
    s3 = FakeS3()
    store = S3UploadStore("bucket", "uploads/", client=s3)
    pending = tmp_path / "pending.pdf"
    pending.write_bytes(b"%PDF-1.7 body")

    store.save(pending, "doc.pdf")

    assert not pending.exists(), "the staged copy is the API's disk, it must not pile up there"
    assert s3.objects == {("bucket", "uploads/doc.pdf"): b"%PDF-1.7 body"}
    with store.local_copy("doc.pdf") as path:
        assert path.endswith("doc.pdf"), "the readers choose by extension"
        assert Path(path).read_bytes() == b"%PDF-1.7 body"
    assert not os.path.exists(path), "the worker's copy is removed once read"

    store.delete("doc.pdf")
    store.delete("doc.pdf")
    assert s3.objects == {}


def test_s3_store_removes_the_staged_file_when_the_upload_fails(tmp_path):
    class Broken(FakeS3):
        def upload_file(self, filename, bucket, key):
            raise ConnectionError("S3 unreachable")

    pending = tmp_path / "pending.pdf"
    pending.write_bytes(b"x")
    with pytest.raises(ConnectionError):
        S3UploadStore("bucket", "uploads/", client=Broken()).save(pending, "doc.pdf")
    assert not pending.exists()


def test_s3_copy_is_removed_even_when_reading_fails():
    s3 = FakeS3()
    s3.objects[("bucket", "uploads/doc.pdf")] = b"x"
    with pytest.raises(ValueError), S3UploadStore("bucket", "uploads/", client=s3).local_copy("doc.pdf") as path:
        raise ValueError("unreadable")
    assert not os.path.exists(path)


def test_local_store_renames_into_its_directory(tmp_path):
    store = LocalUploadStore(str(tmp_path / "uploads"))
    pending = tmp_path / "pending.html"
    pending.write_text("<p>x</p>")

    store.save(pending, "doc.html")

    with store.local_copy("doc.html") as path:
        assert Path(path) == tmp_path / "uploads" / "doc.html"
        assert Path(path).read_text() == "<p>x</p>"
    store.delete("doc.html")
    store.delete("doc.html")
    assert not (tmp_path / "uploads" / "doc.html").exists()


def test_a_job_queued_before_keys_still_finds_its_file_by_path(tmp_path, monkeypatch):
    # Before app/storage.py the payload held the path as the API wrote it, relative or absolute.
    monkeypatch.setattr(settings, "upload_store", "s3")
    monkeypatch.setattr(settings, "upload_s3_bucket", "bucket")
    monkeypatch.setattr(storage, "_s3_client", FakeS3)
    old = tmp_path / "old.html"
    old.write_text("<p>old</p>")

    store, key = uploaded_file({"storage_path": str(old)})

    with store.local_copy(key) as path:
        assert Path(path).read_text() == "<p>old</p>"
    store.delete(key)
    assert not old.exists()


def test_a_key_payload_goes_to_the_configured_store(monkeypatch):
    monkeypatch.setattr(settings, "upload_store", "s3")
    monkeypatch.setattr(settings, "upload_s3_bucket", "bucket")
    monkeypatch.setattr(storage, "_s3_client", FakeS3)

    store, key = uploaded_file({"storage_key": "doc.pdf"})

    assert isinstance(store, S3UploadStore) and key == "doc.pdf"
    assert uploaded_file({}) is None


def test_the_key_is_the_document_id_and_the_lower_case_suffix():
    assert upload_key("abc", "Bai Giang.PDF") == "abc.pdf"


def test_s3_without_a_bucket_is_refused_at_start():
    with pytest.raises(ValueError, match="UPLOAD_S3_BUCKET"):
        Settings(upload_store="s3", upload_s3_bucket="")
