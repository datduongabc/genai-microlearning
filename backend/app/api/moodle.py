import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, is_course_author
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.export.lesson import (
    build_book_chapters,
    build_questions,
    explanations,
    failing_questions,
    find_component,
    plain,
)
from app.generation.module_generation_pipeline import is_control
from app.models import ComponentType, Course, Module, User
from app.moodle import client as moodle_client
from app.moodle.accounts import require_course_author, require_moodle_user_id
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

router = APIRouter(prefix="/modules", tags=["moodle"], dependencies=[Depends(get_current_user)])

class PublishRequest(BaseModel):
    # Which existing Moodle section to add the Book/Quiz into (0 = the
    # course's general/top section) -- see /courses/{id}/moodle-sections for
    # the list a teacher picks from. Defaults to 0 for backwards
    # compatibility with older frontend builds that don't send it yet.
    section_num: int = Field(default=0, ge=0, le=1000)
    # The lecturer confirmed that a module already on Moodle goes there a second time.
    republish: bool = False


_PARTS = (("book_cmid", "Book"), ("practice_cmid", "Practice"), ("quiz_cmid", "Quiz"))


@router.post("/{module_id}/publish-to-moodle")
@limiter.limit("20/minute", key_func=user_or_address)
def publish_to_moodle(
    request: Request,
    module_id: uuid.UUID,
    payload: PublishRequest = PublishRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    module = db.get(Module, module_id)
    if module is None or not is_course_author(db, module.course, current_user):
        raise HTTPException(status_code=404, detail="Module not found")

    if is_control(db, module.id):
        raise HTTPException(
            status_code=422,
            detail="Bản đối chứng chỉ dùng cho nghiên cứu nên không đẩy lên Moodle được.",
        )

    failing = failing_questions(module)
    if failing:
        raise HTTPException(
            status_code=422,
            detail=f"Còn {len(failing)} câu hỏi cần xem lại ({', '.join(failing)}). "
            "Lý do ghi dưới từng câu; hãy sửa trước khi đẩy lên Moodle.",
        )

    course = db.get(Course, module.course_id)
    if course.moodle_course_id is None:
        raise HTTPException(
            status_code=400,
            detail="Course is not linked to a Moodle course yet -- link one first "
            "(POST /courses/{id}/link-moodle)",
        )

    # An unlinked account cannot publish, even when its course link is retained.
    actor_user_id = require_moodle_user_id(current_user)
    chapters = build_book_chapters(module)
    if not chapters:
        raise HTTPException(status_code=422, detail="Mô-đun chưa có nội dung nào để đẩy lên Moodle.")
    # Held until this request commits: a double click or a second tab would otherwise pass the
    # check below too and put everything on Moodle twice. A lock, not a row lock, so editing the
    # module is not held up while Moodle takes its seconds.
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                     {"key": f"moodle-publish:{module.id}"}):
        raise HTTPException(status_code=409, detail="Mô-đun đang được xuất bản lên Moodle. Hãy chờ lần này xong.")
    db.refresh(module)  # what a publish that just finished wrote
    if module.moodle_published_at is not None and not payload.republish:
        raise HTTPException(status_code=409, detail="Mô-đun này đã được xuất bản lên Moodle. Xuất bản lại sẽ tạo thêm một bản nữa.")

    # A publish that failed part way left these on Moodle: they are kept, not created again.
    result = {"moodle_course_id": course.moodle_course_id, **(module.moodle_partial_cmids or {})}
    try:
        # Re-check the linked user before any activity write.
        require_course_author(db, current_user, course.moodle_course_id)
        if "book_cmid" not in result:
            result["book_cmid"] = moodle_client.create_book(
                course.moodle_course_id, plain(module.title) or "Mô-đun", chapters, payload.section_num, actor_user_id=actor_user_id
            )

        # Practice comes before the quiz: it is the rehearsal.
        practice = find_component(module, ComponentType.practice)
        if practice and practice.questions and "practice_cmid" not in result:
            result["practice_cmid"] = moodle_client.create_quiz(
                course.moodle_course_id, f"{plain(module.title)} - Practice",
                build_questions(practice, explanations(module, ComponentType.practice)), payload.section_num,
                actor_user_id=actor_user_id, behaviour="immediatefeedback",
            )

        quiz = find_component(module, ComponentType.quiz)
        if quiz and quiz.questions and "quiz_cmid" not in result:
            result["quiz_cmid"] = moodle_client.create_quiz(
                course.moodle_course_id, f"{plain(module.title)} - Quiz",
                build_questions(quiz, explanations(module, ComponentType.quiz)), payload.section_num,
                actor_user_id=actor_user_id, behaviour="deferredfeedback",
            )
    except MOODLE_FAILURES as e:
        failure = moodle_failure(e, db, course)
        created = [f"{name} (cmid {result[key]})" for key, name in _PARTS if key in result]
        if created:
            module.moodle_partial_cmids = {key: result[key] for key, _ in _PARTS if key in result}
            db.commit()
            failure.detail += (f" Đã có trên Moodle: {', '.join(created)}. Lần xuất bản tiếp theo chỉ tạo các phần "
                               "còn thiếu, nên đừng xóa các hoạt động này.")
        raise failure from e

    module.moodle_published_at = datetime.now(UTC)
    module.moodle_partial_cmids = None
    db.commit()
    return result
