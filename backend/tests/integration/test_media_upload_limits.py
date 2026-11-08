"""A media file over its size limit is refused when the upload is requested, before any document
exists or a byte is sent: the declared size is enough, and S3's policy enforces it afterwards."""
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.documents import UploadRequest, request_upload
from app.models import Course, User

MB = 1024 * 1024


@pytest.mark.parametrize("filename,limit_mb,message", [
    ("lecture.mp3", 200, "Tệp âm thanh tối đa 200 MB"),
    ("lecture.mp4", 1024, "Tệp video tối đa 1 GB"),
])
def test_media_upload_rejects_above_its_limit(filename, limit_mb, message):
    user = User(id=uuid.uuid4())
    course = Course(id=uuid.uuid4(), owner_id=user.id)
    db = MagicMock()
    db.get.return_value = course
    db.query.return_value.filter.return_value.first.return_value = None
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    with pytest.raises(HTTPException) as error:
        request_upload(request=request, course_id=course.id,
                       body=UploadRequest(filename=filename, size=limit_mb * MB + 1), db=db, current_user=user)

    assert error.value.status_code == 413
    assert error.value.detail == message
    db.add.assert_not_called()
    db.commit.assert_not_called()
