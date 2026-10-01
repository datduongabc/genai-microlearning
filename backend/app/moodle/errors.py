"""Safe lecturer-facing Moodle failures, shared by publishing and section lookup."""

import logging

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Course
from app.moodle import client

logger = logging.getLogger(__name__)
MOODLE_FAILURES = (client.MoodleError, httpx.HTTPError)
DELETED_COURSE = (
    "Khóa học trên Moodle đã bị xóa hoặc không còn tồn tại. "
    "Hãy liên kết lại hoặc tạo khóa học Moodle mới."
)


def moodle_failure(error: Exception, db: Session, course: Course | None = None) -> HTTPException:
    logger.error("Moodle operation failed: %s", error)
    message = "Thao tác với Moodle thất bại, vui lòng thử lại sau."
    if isinstance(error, httpx.HTTPError):
        message = "Không kết nối được tới Moodle, vui lòng thử lại sau."
    elif course is not None and course.moodle_course_id is not None:
        # A missing record can be an activity, not the course. Verify before unlinking.
        try:
            missing = client.get_course(course.moodle_course_id) is None
        except MOODLE_FAILURES:
            logger.warning("Could not verify linked Moodle course", exc_info=True)
        else:
            if missing:
                course.moodle_course_id = None
                db.commit()
                message = DELETED_COURSE
    return HTTPException(status_code=502, detail=message)
