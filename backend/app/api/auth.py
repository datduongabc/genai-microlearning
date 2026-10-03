from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.auth.audit import log_auth_event
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    # Only a cap on the size of the body: refuse a huge one before any hashing.
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


ACCOUNT_DISABLED = "Tài khoản đã bị khóa. Hãy liên hệ admin để được hỗ trợ."

# Dummy hash so a login attempt against a nonexistent email takes the same
# time as one against a real email — avoids leaking which emails are registered.
DUMMY_HASH = hash_password(uuid4().hex)


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

    if not user.is_active:
        # Said only after the password was right, so a stranger cannot use it to find out which
        # emails have an account.
        log_auth_event(audit.LOGIN_FAILED, request, user_id=user.id, email=user.email, detail={"reason": "disabled"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ACCOUNT_DISABLED)

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
