"""Model choice checks shared by the routes that start a generation."""
from fastapi import HTTPException

from app.config import settings
from app.generation import fact_check
from app.llm import catalog
from app.models import User


def usable_alias(alias: str) -> str:
    """`alias` nếu gateway phục vụ nó và nó dùng được ngay lúc này, không thì 422."""
    if not catalog.is_available(alias):
        raise HTTPException(
            status_code=422,
            detail=f"Gateway không phục vụ model sinh văn bản nào tên '{alias}'.",
        )
    # Có trong danh sách chưa chắc đã dùng được: khóa của nhà cung cấp có thể trống, hết số
    # dư, hoặc hết hạn mức ngày. Thử ngay bây giờ để báo trong một giây thay vì mười phút.
    # GatewayUnavailable thì để lọt ra, app/main.py đổi nó thành 503.
    try:
        catalog.probe(alias)
    except catalog.ModelUnusable as error:
        raise HTTPException(
            status_code=422,
            detail=f"Model '{error.alias}' chưa dùng được: {error.reason}. Hãy chọn model khác.",
        ) from error
    return alias


def generation_alias(requested: str | None, user: User) -> str:
    """The text model a flashcard set or a video script is written with: the one asked for, else the
    account's default while the gateway serves it, else the configured one.

    The worker fact-checks with a model of another family and fails on an alias that has none;
    refusing here costs nothing, while failing there is after the reserved generation."""
    if requested is not None:
        alias = usable_alias(requested)
    elif user.default_model_alias is not None and catalog.is_available(user.default_model_alias):
        alias = usable_alias(user.default_model_alias)
    else:
        alias = settings.llm_model
    try:
        fact_check.judge_alias(alias)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Model '{alias}' không có model kiểm chứng khác họ.") from error
    return alias
