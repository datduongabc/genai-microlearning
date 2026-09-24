"""Giảng viên xem được những model nào và chọn model nào làm mặc định của mình.

Đặt tên tệp là model_selection chứ không phải models, để không lẫn với app/models/ là
nơi định nghĩa bảng database.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.llm import catalog
from app.models import User

router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(get_current_user)])


class ModelInfo(BaseModel):
    alias: str
    label: str
    provider: str
    note: str | None = None


class ModelList(BaseModel):
    models: list[ModelInfo]
    # Mặc định của chính giảng viên đang gọi, trả cùng danh sách để giao diện chọn sẵn
    # được ngay trong một lần gọi. None nghĩa là chưa chọn, khi đó dùng cấu hình hệ thống.
    default: str | None


class DefaultModel(BaseModel):
    alias: str | None


@router.get("", response_model=ModelList)
def list_models(current_user: User = Depends(get_current_user)):
    return ModelList(
        models=[
            ModelInfo(alias=c.alias, label=c.label, provider=c.provider, note=c.note)
            for c in catalog.available_models()
        ],
        default=current_user.default_model_alias,
    )


@router.put("/default", response_model=ModelList)
@limiter.limit("30/minute", key_func=user_or_address)
def set_default_model(
    request: Request, body: DefaultModel,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    # Kiểm ở biên chứ không tin client: một alias lạ lưu được vào database thì mọi lượt
    # sinh sau đó của giảng viên này hỏng, mà lỗi lại hiện ra ở tận lệnh gọi LLM.
    if body.alias is not None and not catalog.is_available(body.alias):
        raise HTTPException(
            status_code=422,
            detail=f"Gateway không phục vụ model sinh văn bản nào tên '{body.alias}'.",
        )
    if body.alias is not None:
        # Mặc định được dùng cho mọi lượt sinh về sau, nên lưu một model chưa dùng được là để
        # dành một lỗi cho mọi lần bấm sinh tiếp theo. Thử ngay lúc lưu để báo ở đây.
        # GatewayUnavailable thì để lọt ra, app/main.py đổi nó thành 503.
        try:
            catalog.probe(body.alias)
        except catalog.ModelUnusable as loi:
            raise HTTPException(
                status_code=422,
                detail=f"Model '{loi.alias}' chưa dùng được: {loi.ly_do}. Hãy chọn model khác.",
            ) from loi

    user = db.get(User, current_user.id)
    user.default_model_alias = body.alias
    db.commit()

    return ModelList(
        models=[
            ModelInfo(alias=c.alias, label=c.label, provider=c.provider, note=c.note)
            for c in catalog.available_models()
        ],
        default=user.default_model_alias,
    )
