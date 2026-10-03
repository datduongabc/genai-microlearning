"""Pop-up liên kết Moodle chỉ hiện một lần cho mỗi giảng viên: users.moodle_prompted_at.

NULL nghĩa là giảng viên chưa trả lời pop-up. Được đặt khi giảng viên bấm "Có" (hay liên kết tài khoản
Moodle bằng bất cứ cách nào) hoặc "Không"; từ đó pop-up không hiện nữa, kể cả khi liên kết sau này
không còn hợp lệ. app_runtime đã có quyền UPDATE trên toàn bảng users nên không cần cấp thêm.

Revision ID: 0009_moodle_prompted_at
Revises: 0008_user_moodle_link
Create Date: 2026-10-03

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_moodle_prompted_at"
down_revision: str | Sequence[str] | None = "0008_user_moodle_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("moodle_prompted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "moodle_prompted_at")
