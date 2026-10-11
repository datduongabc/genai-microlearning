"""Request a Moodle identity link and activate it only after Moodle-session consent."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.accounts import AccountError, normalize_email
from app.auth.rate_limit import limiter, user_or_address
from app.config import settings
from app.db import get_db
from app.models import User
from app.moodle import client
from app.moodle.accounts import unlink_account
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

router = APIRouter(prefix="/moodle", tags=["moodle"], dependencies=[Depends(get_current_user)])


class LinkRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)


def _lock_account(db: Session, user: User):
    # A concurrent new request or deactivation must not let an old poll activate a link.
    db.refresh(user, with_for_update=True)
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account unavailable")


def _confirmation_url(request_id: str) -> str:
    return settings.moodle_wwwroot.rstrip('/') + '/local/lms_bridge/confirm.php?' + urlencode({'request': request_id})


@router.post('/account/link-requests', status_code=202)
@limiter.limit('10/minute', key_func=user_or_address)
def request_link(request: Request, payload: LinkRequest, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    try:
        email = normalize_email(payload.email)
    except AccountError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    _lock_account(db, current_user)
    if current_user.moodle_user_id is not None:
        raise HTTPException(status_code=409, detail='Tài khoản đã liên kết Moodle.')
    try:
        result = client.request_account_link(app_account=str(current_user.id), email=email,
                                             app_email=current_user.email, app_name=current_user.name)
    except MOODLE_FAILURES as error:
        raise moodle_failure(error, db) from error
    current_user.moodle_request_id = result['request_id']
    current_user.moodle_request_email = email
    db.commit()
    return {'status': 'pending', 'email': email,
            'message': 'Nếu có tài khoản Moodle phù hợp, hãy đăng nhập Moodle để xác nhận liên kết.',
            'confirmation_url': _confirmation_url(result['request_id'])}


@router.get('/account')
@limiter.limit('60/minute', key_func=user_or_address)
def get_account(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _lock_account(db, current_user)
    status = 'not_linked'
    email = current_user.email
    notice = None
    try:
        if current_user.moodle_user_id is None and current_user.moodle_request_id is not None:
            try:
                result = client.account_link_status(request_id=current_user.moodle_request_id,
                                                    app_account=str(current_user.id))
            except client.MoodleError as error:
                if error.errorcode != 'invalidrequest':
                    raise
                current_user.moodle_request_id = None
                current_user.moodle_request_email = None
                db.commit()
                result = {'status': 'not_linked'}
                notice = 'Yêu cầu liên kết không còn hiệu lực trong Moodle, vui lòng gửi lại.'
            else:
                email = current_user.moodle_request_email
            status = result['status']
            if status == 'confirmed':
                current_user.moodle_user_id = int(result['moodle_user_id'])
                current_user.moodle_request_id = None
                current_user.moodle_request_email = None
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    _lock_account(db, current_user)
                    current_user.moodle_request_id = None
                    current_user.moodle_request_email = None
                    db.commit()
                    status = 'not_linked'
                    email = current_user.email
                    notice = 'Tài khoản Moodle đã liên kết với một tài khoản khác.'
        if current_user.moodle_user_id is not None:
            info = client.teacher_courses(current_user.moodle_user_id)
            if not info['exists']:
                current_user.moodle_user_id = None
                db.commit()
                status = 'not_linked'
                email = current_user.email
            else:
                status = 'linked'
                email = info['email']
    except MOODLE_FAILURES as error:
        raise moodle_failure(error, db) from error
    answer = {'status': status, 'email': email}
    if status == 'pending':
        answer['confirmation_url'] = _confirmation_url(current_user.moodle_request_id)
    if notice:
        answer['error'] = notice
    return answer


@router.delete('/account')
def unlink_current_account(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    unlink_account(db, current_user)
    return {'status': 'not_linked', 'email': current_user.email}
