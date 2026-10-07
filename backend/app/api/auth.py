from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.auth.audit import log_auth_event
from app.auth.moodle_login import (
    ACCOUNT_DISABLED,
    WRONG_CREDENTIALS,
    LoginRefused,
    login_with_moodle,
)
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token, verify_password
from app.db import get_db
from app.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    # A lecturer's Moodle username, or an admin's email. Only caps on the size of the body: refuse a
    # huge one before any hashing or call to Moodle.
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(max_length=1024, repr=False)

    @field_validator("username")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshRequest(BaseModel):
    # A real token is 43 characters; the cap only keeps a huge body away from the hash.
    refresh_token: str = Field(max_length=200)


class AccessTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """An admin logs in with their email and the local password made by scripts/create_admin.py;
    everyone else is a lecturer, who logs in with their Moodle username and password (the app asks
    Moodle, app/auth/moodle_login.py). Moodle being down stops lecturers only."""
    username = payload.username
    admin = db.query(User).filter(User.role == UserRole.admin, func.lower(User.email) == username.lower()).first()
    if admin is not None:
        user = _local_login(request, db, admin, payload.password)
        via = "local"
    else:
        user, via = _moodle_login(request, db, username, payload.password)

    refresh_token = sessions.start_session(db, user.id)
    access_token = create_access_token(subject=user.id)
    db.commit()
    log_auth_event(audit.LOGIN_SUCCESS, request, user_id=user.id, email=user.email, detail={"via": via})
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse(id=str(user.id), email=user.email, name=user.name, role=user.role),
    )


def _local_login(request: Request, db: Session, admin: User, password: str) -> User:
    if not verify_password(password, admin.hashed_password):
        # The reason goes to the log only: the client hears the same thing either way.
        log_auth_event(
            audit.LOGIN_FAILED, request, user_id=admin.id, email=admin.email, detail={"reason": "wrong_password"}
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=WRONG_CREDENTIALS)
    if not admin.is_active:
        # Said only after the password was right, so a stranger cannot use it to find out which
        # emails have an account.
        log_auth_event(audit.LOGIN_FAILED, request, user_id=admin.id, email=admin.email, detail={"reason": "disabled"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ACCOUNT_DISABLED)
    return admin


def _moodle_login(request: Request, db: Session, username: str, password: str) -> tuple[User, str]:
    try:
        found = login_with_moodle(db, username, password)
    except LoginRefused as refused:
        log_auth_event(
            audit.LOGIN_FAILED, request, email=username.lower() if "@" in username else None,
            detail={"reason": refused.reason, "via": "moodle"},
        )
        raise HTTPException(status_code=refused.status_code, detail=refused.detail) from None
    if found.outcome != "existing":
        log_auth_event(
            audit.MOODLE_ACCOUNT_LINKED, request, user_id=found.user.id, email=found.user.email,
            detail={"created": found.outcome == "created"},
        )
    return found.user, "moodle"


@router.post("/refresh", response_model=AccessTokenResponse)
@limiter.limit("30/minute")
def refresh(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        user_id, new_refresh_token = sessions.rotate_refresh_token(db, payload.refresh_token)
    except sessions.RefreshRejected as rejected:
        log_auth_event(audit.REFRESH_REJECTED, request, user_id=rejected.user_id, detail={"reason": rejected.reason})
        # Whatever the reason (unknown, expired, spent) the client hears the same thing,
        # so a probe learns nothing about the token.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from None
    access_token = create_access_token(subject=user_id)
    db.commit()
    return AccessTokenResponse(access_token=access_token, refresh_token=new_refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
def logout(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    # Answers 204 for a token it does not know too, so logout is not a way to test tokens.
    user_id = sessions.end_session(db, payload.refresh_token)
    if user_id is not None:
        log_auth_event(audit.LOGOUT, request, user_id=user_id)
