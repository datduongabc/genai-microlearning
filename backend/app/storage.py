"""Where an uploaded file waits between the upload and the document pipeline job.

The API and the worker are separate processes, and on ECS separate tasks with separate disks, so
the file has to be somewhere both can reach. Each store is addressed by a key, the file name
`<document id><suffix>`, and the readers still get a local path: S3 is copied down to a temporary
file for the length of the read.

On S3 the browser sends the file itself, through a presigned POST whose policy caps its size, so a
1 GB video never passes through an API task. The local store has no such URL: the API receives the
file at PUT /documents/{id}/content instead (app/api/documents.py).
"""

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from sqlalchemy import select

from app.config import settings
from app.models.media import Video

# How long a presigned POST stays valid. A 1 GB video over a 10 Mbit/s line takes about 14 minutes;
# S3 checks the expiry when the upload starts, not when it ends.
PRESIGNED_POST_SECONDS = 15 * 60
# How long a download link stays valid: S3 checks it when the download starts, and the page asks
# for a fresh one on every click.
PRESIGNED_GET_SECONDS = 5 * 60

logger = logging.getLogger(__name__)


# Without a type, S3 serves a file as binary/octet-stream, and a browser given a presigned link to
# it downloads the video instead of playing it.
_MEDIA_TYPES = {".mp4": "video/mp4", ".wav": "audio/wav", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png"}


class UploadStore(Protocol):
    def save(self, local_path: Path, key: str, content_type: str | None = None) -> None:
        """Take the file at local_path under key. local_path is gone afterwards."""

    def local_copy(self, key: str) -> AbstractContextManager[str]:
        """A local path to the file for the length of the with block."""

    def delete(self, key: str) -> None:
        """Remove the file. Nothing happens when there is none."""

    def size(self, key: str) -> int | None:
        """The stored file's size in bytes, or None when nothing is stored under key."""


class LocalUploadStore:
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / key

    def path(self, key: str) -> Path:
        """Where the file is, for the API to send it itself: this store has no download URL."""
        return self._path(key)

    def save(self, local_path: Path, key: str, content_type: str | None = None) -> None:
        # The type is S3's to send; the API serves local files by their extension.
        # A key may carry a folder (media/videos/<id>/...), which S3 needs no setup for.
        self._path(key).parent.mkdir(parents=True, exist_ok=True)
        os.replace(local_path, self._path(key))

    @contextmanager
    def local_copy(self, key: str) -> Iterator[str]:
        yield str(self._path(key))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def size(self, key: str) -> int | None:
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None


class S3UploadStore:
    def __init__(self, bucket: str, prefix: str, client=None):
        self.bucket = bucket
        self.prefix = prefix
        self.client = client if client is not None else _s3_client()

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def save(self, local_path: Path, key: str, content_type: str | None = None) -> None:
        """`content_type` defaults to the one of the key's extension, for the media a browser plays."""
        content_type = content_type or _MEDIA_TYPES.get(Path(key).suffix.lower())
        # Only when known, so that S3 keeps choosing for the uploads it always has.
        extra = {"ExtraArgs": {"ContentType": content_type}} if content_type else {}
        try:
            # upload_file goes multipart above 8 MB, so a 1 GB video never sits in memory.
            self.client.upload_file(str(local_path), self.bucket, self._object_key(key), **extra)
        finally:
            local_path.unlink(missing_ok=True)

    @contextmanager
    def local_copy(self, key: str) -> Iterator[str]:
        directory = tempfile.mkdtemp(prefix="upload-")
        try:
            # The readers choose by extension, so the copy keeps the key's name.
            path = os.path.join(directory, os.path.basename(key))
            self.client.download_file(self.bucket, self._object_key(key), path)
            yield path
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def delete(self, key: str) -> None:
        # S3 answers a delete of a missing object with success, so there is nothing to check.
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))["ContentLength"]
        except ClientError as error:
            # HEAD has no body, so a missing object is only a status code.
            if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def presigned_post(self, key: str, max_bytes: int) -> dict:
        """Where and how the browser sends the file: {"url", "fields"} of a form POST.

        S3 itself refuses a body outside 1..max_bytes, and the key is fixed, so the URL can only
        ever put this one document's file."""
        return self.client.generate_presigned_post(
            Bucket=self.bucket, Key=self._object_key(key),
            Conditions=[["content-length-range", 1, max_bytes]],
            ExpiresIn=PRESIGNED_POST_SECONDS,
        )

    def presigned_get(self, key: str, filename: str) -> str:
        """A link that downloads the file under filename, valid for PRESIGNED_GET_SECONDS.

        The browser follows it as a plain navigation, so the bucket needs no CORS rule for GET."""
        quoted = quote(filename)
        return self.client.generate_presigned_url(
            "get_object", ExpiresIn=PRESIGNED_GET_SECONDS, Params={
                "Bucket": self.bucket, "Key": self._object_key(key),
                "ResponseContentDisposition": f"attachment; filename*=UTF-8''{quoted}",
            })


@cache
def _s3_client():
    import boto3
    from botocore.config import Config

    # Made on first use and shared (boto3 clients are thread safe), so a process that never
    # touches an upload needs no AWS setup. The region and virtual-hosted addressing make a
    # presigned POST name the bucket's regional endpoint: the global one answers a browser in
    # another region with a redirect, which a cross-origin POST does not follow.
    return boto3.client(
        "s3", region_name=settings.upload_s3_region or None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def upload_store() -> UploadStore:
    if settings.upload_store == "s3":
        return S3UploadStore(settings.upload_s3_bucket, settings.upload_s3_prefix)
    return LocalUploadStore(settings.upload_storage_dir)


def study_file_store() -> UploadStore:
    """Where a study's reference files are kept, for as long as the study keeps them."""
    if settings.upload_store == "s3":
        return S3UploadStore(settings.upload_s3_bucket, settings.study_files_s3_prefix)
    return LocalUploadStore(str(Path(settings.upload_storage_dir) / "study-files"))


def media_store() -> UploadStore:
    """Where the images, voice and rendered videos of a document are kept, as long as the document is."""
    if settings.upload_store == "s3":
        return S3UploadStore(settings.upload_s3_bucket, settings.media_s3_prefix)
    return LocalUploadStore(str(Path(settings.upload_storage_dir) / "media"))


def _media_keys(db, *conditions) -> list[str]:
    """The stored files of the videos matching the conditions: the video, and each scene's image and voice.

    Read before the rows are deleted, because they go by cascade and the keys only live in them."""
    keys: list[str] = []
    for video_key, scenes in db.execute(select(Video.video_key, Video.scenes).where(*conditions)):
        named = (s.get(field) for s in scenes or [] for field in ("image_key", "voice_key"))
        keys.extend(key for key in (video_key, *named) if key)
    return keys


def media_keys_for_video(db, video_id) -> list[str]:
    return _media_keys(db, Video.id == video_id)


def media_keys_for_document(db, document_id) -> list[str]:
    return _media_keys(db, Video.document_id == document_id)


def media_keys_for_course(db, course_id) -> list[str]:
    return _media_keys(db, Video.course_id == course_id)


def delete_media_files(keys: list[str]) -> None:
    """Delete after the rows' deletion is committed: a file without its row is harmless, a row
    without its file is a video that cannot play."""
    store = media_store()
    for key in keys:
        store.delete(key)


def copy_file(source: UploadStore, source_key: str, target: UploadStore, target_key: str) -> None:
    """Copies a stored file. Between two prefixes of one bucket S3 copies it itself, so the bytes
    never pass through this process; otherwise through a local copy."""
    if isinstance(source, S3UploadStore) and isinstance(target, S3UploadStore) and source.bucket == target.bucket:
        target.client.copy_object(Bucket=target.bucket, Key=target._object_key(target_key),
                                  CopySource={"Bucket": source.bucket, "Key": source._object_key(source_key)})
        return
    with source.local_copy(source_key) as path:
        # In the staging directory, which for the local store is on its disk, so save() can rename.
        copied = Path(tempfile.mkdtemp(prefix="copy-", dir=staging_dir())) / Path(path).name
        shutil.copyfile(path, copied)
        try:
            target.save(copied, target_key)
        finally:
            shutil.rmtree(copied.parent, ignore_errors=True)


def study_file_key(file_id, filename: str) -> str:
    return f"{file_id}{Path(filename).suffix.lower()}"


def uploaded_file(payload: dict) -> tuple[UploadStore, str] | None:
    """The store and key of a document pipeline job's file."""
    if "storage_key" in payload:
        return upload_store(), payload["storage_key"]
    return None


def upload_key(document_id, filename: str) -> str:
    return f"{document_id}{Path(filename).suffix.lower()}"


def staging_dir() -> Path:
    """Where PUT /documents/{id}/content writes the file while it checks the size.

    The local store's own directory, so that save() is a rename. For S3 the system temp dir."""
    directory = Path(settings.upload_storage_dir if settings.upload_store == "local" else tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    return directory
