import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
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
    if module is None or module.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Module not found")

    if is_control(db, module.id):
        raise HTTPException(
            status_code=422,
            detail="Mô-đun đối chứng (không RAG) chỉ dùng cho nghiên cứu, không được đẩy lên Moodle.",
        )

    failing = failing_questions(module)
    if failing:
        raise HTTPException(
            status_code=422,
            detail="Còn câu hỏi chưa đạt gate chất lượng; sửa hoặc sinh lại trước khi đẩy lên Moodle: "
            + "; ".join(failing),
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

    try:
        # Re-check the linked user before any activity write.
        require_course_author(db, current_user, course.moodle_course_id)
        result = {
            "moodle_course_id": course.moodle_course_id,
            "book_cmid": moodle_client.create_book(
                course.moodle_course_id, plain(module.title) or "Mô-đun", chapters, payload.section_num, actor_user_id=actor_user_id
            ),
        }

        # Practice comes before the quiz: it is the rehearsal.
        practice = find_component(module, ComponentType.practice)
        if practice and practice.questions:
            result["practice_cmid"] = moodle_client.create_quiz(
                course.moodle_course_id, f"{plain(module.title)} - Practice",
                build_questions(practice, explanations(module, ComponentType.practice)), payload.section_num,
                actor_user_id=actor_user_id, behaviour="immediatefeedback",
            )

        quiz = find_component(module, ComponentType.quiz)
        if quiz and quiz.questions:
            result["quiz_cmid"] = moodle_client.create_quiz(
                course.moodle_course_id, f"{plain(module.title)} - Quiz",
                build_questions(quiz, explanations(module, ComponentType.quiz)), payload.section_num,
                actor_user_id=actor_user_id, behaviour="deferredfeedback",
            )
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db, course) from e

    return result
