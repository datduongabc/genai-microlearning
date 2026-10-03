from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.security import decode_token, token_user_id
from app.db import get_db
from app.models import Course, User, UserRole

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


def require_course_owner(course: Course | None, user: User) -> Course:
    """Raise 404 (not 403) for both "course doesn't exist" and "not yours" --
    same not-found-either-way shape as chat.py's session check, so an
    unauthorized course_id can't be distinguished from a nonexistent one."""
    if course is None or course.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Admin endpoints answer 404 to anyone else, the same as for something that does not exist
    (see require_course_owner), so a lecturer learns nothing about what is there."""
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return current_user
