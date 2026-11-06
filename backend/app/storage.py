"""Where an uploaded file waits between the upload request and the document pipeline job.

The API and the worker are separate processes, and on ECS separate tasks with separate disks, so
the file has to be somewhere both can reach. Each store is addressed by a key, the file name
`<document id><suffix>`, and the readers still get a local path: S3 is copied down to a temporary
file for the length of the read.
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

from app.config import settings

logger = logging.getLogger(__name__)


class UploadStore(Protocol):
    def save(self, local_path: Path, key: str) -> None:
        """Take the file at local_path under key. local_path is gone afterwards."""

    def local_copy(self, key: str) -> AbstractContextManager[str]:
        """A local path to the file for the length of the with block."""

    def delete(self, key: str) -> None:
        """Remove the file. Nothing happens when there is none."""


class LocalUploadStore:
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / key

    def save(self, local_path: Path, key: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.replace(local_path, self._path(key))

    @contextmanager
    def local_copy(self, key: str) -> Iterator[str]:
        yield str(self._path(key))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3UploadStore:
    def __init__(self, bucket: str, prefix: str, client=None):
        self.bucket = bucket
        self.prefix = prefix
        self.client = client if client is not None else _s3_client()

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def save(self, local_path: Path, key: str) -> None:
        try:
            # upload_file goes multipart above 8 MB, so a 1 GB video never sits in memory.
            self.client.upload_file(str(local_path), self.bucket, self._object_key(key))
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


@cache
def _s3_client():
    import boto3

    # Made on first use and shared (boto3 clients are thread safe), so a process that never
    # touches an upload needs no AWS setup.
    return boto3.client("s3")


def upload_store() -> UploadStore:
    if settings.upload_store == "s3":
        return S3UploadStore(settings.upload_s3_bucket, settings.upload_s3_prefix)
    return LocalUploadStore(settings.upload_storage_dir)


def uploaded_file(payload: dict) -> tuple[UploadStore, str] | None:
    """The store and key of a document pipeline job's file.

    A job enqueued before keys existed names its file by `storage_path`, a path as the API saw it
    on the shared disk, which a store rooted nowhere reads as it is."""
    if "storage_key" in payload:
        return upload_store(), payload["storage_key"]
    if "storage_path" in payload:
        return LocalUploadStore(""), payload["storage_path"]
    return None


def upload_key(document_id, filename: str) -> str:
    return f"{document_id}{Path(filename).suffix.lower()}"


def staging_dir() -> Path:
    """Where the upload request writes the file while it checks the size and the page count.

    The local store's own directory, so that save() is a rename. For S3 the system temp dir:
    the file only lives there until it has been sent."""
    directory = Path(settings.upload_storage_dir if settings.upload_store == "local" else tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    return directory
