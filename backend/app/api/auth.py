from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.auth.accounts import AccountError, create_account
from app.auth.audit import log_auth_event
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(max_length=1024, repr=False)


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(max_length=1024, repr=False)

    @field_validator("email")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip().lower()


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    is_admin: bool


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


DUMMY_HASH = hash_password("Timing sentinel local login")
WRONG_CREDENTIALS = "Email hoặc mật khẩu không đúng."
ACCOUNT_DISABLED = "Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ admin."


@router.post("/register", response_model=UserResponse, status_code=201)
@limiter.limit("10/minute")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user = create_account(db, email=payload.email, name=payload.name, password=payload.password)
    except AccountError as error:
        log_auth_event(audit.REGISTER_REJECTED, request, email=payload.email, detail={"status": error.status_code})
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    log_auth_event(audit.REGISTER_SUCCESS, request, user_id=user.id, email=user.email)
    return UserResponse(id=str(user.id), email=user.email, name=user.name, is_admin=user.is_admin)


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == payload.email).first()
    password_ok = verify_password(payload.password, user.hashed_password if user else DUMMY_HASH)
    if user is None or not password_ok:
        log_auth_event(audit.LOGIN_FAILED, request, email=payload.email, detail={"reason": "credentials"})
        raise HTTPException(status_code=401, detail=WRONG_CREDENTIALS)
    if not user.is_active:
        log_auth_event(audit.LOGIN_FAILED, request, user_id=user.id, email=user.email, detail={"reason": "disabled"})
        raise HTTPException(status_code=403, detail=ACCOUNT_DISABLED)
    refresh_token = sessions.start_session(db, user.id)
    access_token = create_access_token(subject=user.id)
    db.commit()
    log_auth_event(audit.LOGIN_SUCCESS, request, user_id=user.id, email=user.email)
    return Token(access_token=access_token, refresh_token=refresh_token,
                 user=UserResponse(id=str(user.id), email=user.email, name=user.name, is_admin=user.is_admin))


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
