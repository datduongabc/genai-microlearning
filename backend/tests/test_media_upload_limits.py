"""Reject oversized streams before storing a document or queuing extraction."""
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

from app.api.documents import upload_document
from app.models import Course, User


@pytest.mark.parametrize("filename,limit_mb,message", [
    ("lecture.mp3", 200, "Tệp âm thanh tối đa 200 MB"),
    ("lecture.mp4", 500, "Tệp video tối đa 500 MB"),
])
def test_media_upload_rejects_above_its_limit(filename, limit_mb, message):
    user = User(id=uuid.uuid4())
    course = Course(id=uuid.uuid4(), owner_id=user.id)
    db = MagicMock()
    db.get.return_value = course
    db.query.return_value.filter.return_value.first.return_value = None
    stream = MagicMock()
    # Generate one MB at a time rather than constructing a 500 MB multipart body.
    stream.read.return_value = b"x" * (1024 * 1024)
    file = UploadFile(filename=filename, file=stream)
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    with pytest.raises(HTTPException) as error:
        upload_document(request=request, course_id=course.id, file=file, db=db, current_user=user)
    assert error.value.status_code == 413
    assert error.value.detail == message
    assert stream.read.call_count == limit_mb + 1
    db.add.assert_not_called()
    db.commit.assert_not_called()
