"""Model choice checks shared by the routes that start a generation."""
from fastapi import HTTPException

from app.llm import catalog


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
