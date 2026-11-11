"""Safe lecturer-facing Moodle failures, shared by publishing and section lookup."""

import logging

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Course
from app.moodle import client

logger = logging.getLogger(__name__)
MOODLE_FAILURES = (client.MoodleError, client.MoodleNotConfigured, httpx.HTTPError)
MOODLE_NOT_CONFIGURED = (
    "Hệ thống này chưa được nối với Moodle qua web service, nên chưa liên kết tài khoản hay xuất bản "
    "sang Moodle được. Mở công cụ từ Moodle (LTI) vẫn dùng được."
)
# Moodle's errorcode when it refuses the token or the call (webservice_access_exception: the
# token is unknown, the user may not use web services, the function is not allowed...) -- a
# setup problem on the Moodle side that retrying cannot fix.
_MOODLE_REFUSED_CODES = ("accessexception", "invalidtoken")
MOODLE_REFUSED = (
    "Moodle từ chối yêu cầu do cấu hình quyền truy cập chưa đúng. "
    "Vui lòng liên hệ quản trị viên để kiểm tra kết nối với Moodle."
)
DELETED_COURSE = (
    "Khóa học trên Moodle đã bị xóa hoặc không còn tồn tại. "
    "Hãy nhờ admin Moodle kiểm tra khóa học rồi liên kết lại."
)


def moodle_failure(error: Exception, db: Session, course: Course | None = None) -> HTTPException:
    if isinstance(error, client.MoodleNotConfigured):
        # A configuration, not a failure: retrying cannot help, and nothing went wrong to alarm on.
        logger.info("Moodle not configured: %s", error)
        return HTTPException(status_code=503, detail=MOODLE_NOT_CONFIGURED)
    logger.error("Moodle operation failed: %s", error)
    message = "Thao tác với Moodle thất bại, vui lòng thử lại sau."
    if isinstance(error, httpx.HTTPError):
        message = "Không kết nối được tới Moodle, vui lòng thử lại sau."
    elif isinstance(error, client.MoodleError) and error.errorcode in _MOODLE_REFUSED_CODES:
        message = MOODLE_REFUSED
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
