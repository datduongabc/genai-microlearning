from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.auth.audit import log_auth_event
from app.auth.password_policy import check_password
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    # Only a cap on the size of the body here: the length in bytes and the strength
    # rules are checked in register() (password_policy), which answers in words the
    # person can act on instead of a schema error.
    password: str = Field(max_length=1024)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        # Case differences ("A@x.com" vs "a@x.com") must not create two
        # distinct-looking accounts for what a user considers the same email.
        return v.strip().lower()

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Họ tên không được để trống")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    # Same reason as RegisterRequest.password: refuse a huge body before any hashing.
    password: str = Field(max_length=1024)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


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


# Dummy hash so a login attempt against a nonexistent email takes the same
# time as one against a real email — avoids leaking which emails are registered.
DUMMY_HASH = hash_password(uuid4().hex)


def _refuse_sign_up(request: Request, email: str, reason: str, status_code: int, detail: str) -> NoReturn:
    log_auth_event(audit.REGISTER_REJECTED, request, email=email, detail={"reason": reason})
    raise HTTPException(status_code=status_code, detail=detail)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    weakness = check_password(payload.password, email=payload.email, name=payload.name)
    if weakness is not None:
        _refuse_sign_up(request, payload.email, "weak_password", status.HTTP_422_UNPROCESSABLE_CONTENT, weakness)

    taken = "Email already registered"
    if db.query(User).filter(User.email == payload.email).first() is not None:
        _refuse_sign_up(request, payload.email, "email_taken", status.HTTP_409_CONFLICT, taken)

    user = User(
        email=payload.email,
        name=payload.name,
        hashed_password=hash_password(payload.password),
        role=UserRole.teacher,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # Two sign-ups with one email that both passed the check above: the unique
        # index lets exactly one in, and the other is a conflict like any duplicate.
        db.rollback()
        _refuse_sign_up(request, payload.email, "email_taken", status.HTTP_409_CONFLICT, taken)
    db.commit()
    log_auth_event(audit.REGISTER, request, user_id=user.id, email=user.email)
    db.refresh(user)
    return UserResponse(id=str(user.id), email=user.email, name=user.name, role=user.role)


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    target_hash = user.hashed_password if user else DUMMY_HASH

    if not verify_password(payload.password, target_hash) or user is None:
        # The reason goes to the log only: the client hears the same thing either way.
        log_auth_event(
            audit.LOGIN_FAILED, request, user_id=user.id if user else None, email=payload.email,
            detail={"reason": "wrong_password" if user else "unknown_email"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    refresh_token = sessions.start_session(db, user.id)
    access_token = create_access_token(subject=user.id)
    db.commit()
    log_auth_event(audit.LOGIN_SUCCESS, request, user_id=user.id, email=user.email)
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse(id=str(user.id), email=user.email, name=user.name, role=user.role),
    )


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
