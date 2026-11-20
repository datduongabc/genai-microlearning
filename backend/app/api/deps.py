from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.security import decode_token, token_user_id
from app.config import settings
from app.db import get_db
from app.generation.limits import today
from app.models import Course, CourseMember, User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    claims = decode_token(credentials.credentials)
    user_id = token_user_id(claims)
    if user_id is None:
        raise unauthorized

    # users KHÔNG có RLS (xem migration 881bca16e9bc) nên tra cứu này hoạt
    # động đúng dù session var chưa set -- đúng ý, phải biết user là ai
    # TRƯỚC KHI có thể set session var theo đúng user đó.
    user = db.get(User, user_id)
    # is_active is read here, from the database, on every request: a locked account is shut out
    # at once, with the access token it already holds, and the token carries no role either.
    if user is None or not user.is_active:
        raise unauthorized

    # Set cho transaction HIỆN TẠI (đã mở sẵn từ câu query User ở trên) +
    # lưu vào session.info để after_begin (app/db.py) tự set lại ở MỌI
    # transaction sau đó trong cùng request (kể cả sau khi route handler
    # commit() giữa chừng, SET LOCAL trước đó đã hết hiệu lực).
    db.info["rls_user_id"] = user.id
    # SET/SET LOCAL không nhận bind parameter (giới hạn cú pháp Postgres,
    # không phải SQLAlchemy) -- an toàn nội suy thẳng vì user.id đã là
    # uuid.UUID thật (round-trip qua str() rồi ghép vào chuỗi chỉ có thể ra
    # hex digit + dấu gạch ngang, không có ký tự đặc biệt nào lọt qua được).
    db.execute(text(f"SET LOCAL app.current_user_id = '{user.id}'"))

    return user


def is_course_author(db: Session, course: Course, user: User) -> bool:
    """The owner, or another instructor of the course who joined it as a teacher (alembic 0049): who
    writes documents, modules, flashcards and videos under it. Row level security says the same."""
    return course.owner_id == user.id or db.scalar(select(CourseMember.role).where(
        CourseMember.course_id == course.id, CourseMember.user_id == user.id)) == "teacher"


def require_authored_course(db: Session, course: Course | None, user: User) -> Course:
    """Raise 404 (not 403) for both "course doesn't exist" and "not yours" --
    same not-found-either-way shape as chat.py's session check, so an
    unauthorized course_id can't be distinguished from a nonexistent one."""
    if course is None or not is_course_author(db, course, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Admin endpoints answer 404 to anyone else, the same as for something that does not exist
    (see require_course_owner), so a lecturer learns nothing about what is there."""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return current_user


def require_course_reader(db: Session, course: Course | None, user: User) -> Course:
    """The owner, or a member: someone who may chat with the course's material. 404 otherwise, as for an owner
    check. Row level security already hides the course from anyone else; this says the same in the code."""
    if course is not None and course.owner_id == user.id:
        return course
    if course is None or db.scalar(select(CourseMember.user_id).where(
            CourseMember.course_id == course.id, CourseMember.user_id == user.id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def require_author(user: User) -> User:
    """Creating courses and everything under them. An account an LMS made for a student cannot."""
    if not user.can_author:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Tài khoản sinh viên chỉ dùng được chat của khóa học.")
    return user


def count_learner_chat(db: Session, user: User) -> None:
    """One chat message against the day's limit of an account that cannot author. In the request's transaction,
    with the account row locked, so two messages at once cannot both take the last one."""
    if user.can_author:
        return
    db.refresh(user, with_for_update=True)
    day = today()
    used = user.chat_count if user.chat_day == day else 0
    if used >= settings.learner_daily_chat_limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=(
            f"Bạn đã hỏi đủ {settings.learner_daily_chat_limit} câu hôm nay. Hãy quay lại vào ngày mai."))
    user.chat_day = day
    user.chat_count = used + 1
