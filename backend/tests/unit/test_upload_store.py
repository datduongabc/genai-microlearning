"""The upload stores: the API and the worker run as separate ECS tasks, so the file must reach the
worker through S3, and the local store must keep working for compose and older queued jobs."""

import os
from pathlib import Path

import pytest

from app import storage
from app.config import Settings, settings
from app.storage import LocalUploadStore, S3UploadStore, upload_key, uploaded_file


class FakeS3:
    """Stands in for boto3's S3 client: the calls the store makes, kept in a dict.

    browser_post() plays the browser sending a presigned POST, and S3 checking its policy."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.extra_args: dict[tuple[str, str], dict | None] = {}
        self.policies: dict[str, tuple[str, int]] = {}

    def generate_presigned_post(self, Bucket, Key, Conditions, ExpiresIn):
        [(name, low, high)] = Conditions
        assert name == "content-length-range" and low == 1
        url = f"https://{Bucket}.s3.test.amazonaws.com/"
        self.policies[Key] = (Bucket, high)
        return {"url": url, "fields": {"key": Key, "policy": "signed", "x-amz-expires": ExpiresIn}}

    def browser_post(self, target, content: bytes):
        key = target["fields"]["key"]
        bucket, high = self.policies[key]
        assert target["url"] == f"https://{bucket}.s3.test.amazonaws.com/"
        if not 1 <= len(content) <= high:
            raise AssertionError("S3 refuses a body outside the policy's content-length-range")
        self.objects[(bucket, key)] = content

    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.objects[(bucket, key)] = Path(filename).read_bytes()
        self.extra_args[(bucket, key)] = ExtraArgs

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


@pytest.mark.parametrize(("key", "content_type"), [
    ("videos/v/video-1-abc.mp4", "video/mp4"),
    ("videos/v/voice-0-abc.wav", "audio/wav"),
    ("videos/v/img-0-abc.jpg", "image/jpeg"),
])
def test_s3_media_files_carry_their_content_type(tmp_path, key, content_type):
    """A browser plays a presigned video or voice only when S3 sends its type, not binary/octet-stream."""
    s3 = FakeS3()
    pending = tmp_path / "pending"
    pending.write_bytes(b"x")
    S3UploadStore("bucket", "media/", client=s3).save(pending, key)
    assert s3.extra_args[("bucket", f"media/{key}")] == {"ContentType": content_type}


def test_an_explicit_content_type_wins_and_an_unknown_one_is_left_to_s3(tmp_path):
    s3 = FakeS3()
    store = S3UploadStore("bucket", "uploads/", client=s3)
    for name, given in (("a.bin", "application/x-thing"), ("doc.pdf", None)):
        pending = tmp_path / name
        pending.write_bytes(b"x")
        store.save(pending, name, content_type=given)
    assert s3.extra_args[("bucket", "uploads/a.bin")] == {"ContentType": "application/x-thing"}
    assert s3.extra_args[("bucket", "uploads/doc.pdf")] is None


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


def test_s3_presigned_post_fixes_the_key_and_caps_the_size():
    s3 = FakeS3()
    store = S3UploadStore("bucket", "uploads/", client=s3)

    target = store.presigned_post("doc.mp4", 1000)

    assert target["fields"]["key"] == "uploads/doc.mp4"
    assert target["fields"]["x-amz-expires"] == storage.PRESIGNED_POST_SECONDS == 15 * 60
    with pytest.raises(AssertionError, match="content-length-range"):
        s3.browser_post(target, b"x" * 1001)
    s3.browser_post(target, b"x" * 1000)
    assert store.size("doc.mp4") == 1000


def test_size_is_none_when_nothing_was_stored(tmp_path):
    assert S3UploadStore("bucket", "uploads/", client=FakeS3()).size("missing.pdf") is None
    local = LocalUploadStore(str(tmp_path))
    assert local.size("missing.pdf") is None
    (tmp_path / "there.pdf").write_bytes(b"abc")
    assert local.size("there.pdf") == 3


def test_s3_size_lets_other_errors_through():
    from botocore.exceptions import ClientError

    class Denied(FakeS3):
        def head_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    with pytest.raises(ClientError):
        S3UploadStore("bucket", "uploads/", client=Denied()).size("doc.pdf")


def test_the_real_client_presigns_for_the_buckets_regional_endpoint(monkeypatch):
    # No network: generate_presigned_post only signs. A browser cannot follow the global
    # endpoint's redirect on a cross-origin POST, so the URL must name the region.
    monkeypatch.setattr(settings, "upload_s3_region", "ap-southeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    storage._s3_client.cache_clear()
    try:
        target = S3UploadStore("my-bucket", "uploads/").presigned_post("doc.pdf", 10)
    finally:
        storage._s3_client.cache_clear()

    assert target["url"] == "https://my-bucket.s3.ap-southeast-1.amazonaws.com/"
    assert target["fields"]["key"] == "uploads/doc.pdf"


def test_a_download_link_names_the_file_and_stays_in_the_prefix(monkeypatch):
    # Signed offline, like the POST above. A Vietnamese file name must survive the header.
    monkeypatch.setattr(settings, "upload_s3_region", "ap-southeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    storage._s3_client.cache_clear()
    try:
        url = S3UploadStore("my-bucket", "study-files/").presigned_get("f1.pdf", "Bài giảng 3.pdf")
    finally:
        storage._s3_client.cache_clear()

    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url)
    assert parsed.netloc == "my-bucket.s3.ap-southeast-1.amazonaws.com"
    assert parsed.path == "/study-files/f1.pdf"
    query = parse_qs(parsed.query)
    assert query["response-content-disposition"] == ["attachment; filename*=UTF-8''B%C3%A0i%20gi%E1%BA%A3ng%203.pdf"]
    assert query["X-Amz-Expires"] == [str(storage.PRESIGNED_GET_SECONDS)]


def test_study_files_have_their_own_place(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))
    assert storage.study_file_store().path("f.pdf") == tmp_path / "study-files" / "f.pdf"
    monkeypatch.setattr(settings, "upload_store", "s3")
    monkeypatch.setattr(settings, "upload_s3_bucket", "b")
    store = storage.study_file_store()
    assert (store.bucket, store.prefix) == ("b", "study-files/")
    assert storage.study_file_key("f", "Slides.PPTX") == "f.pptx"
