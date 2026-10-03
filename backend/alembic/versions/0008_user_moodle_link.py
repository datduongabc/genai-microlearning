"""Liên kết tài khoản Moodle mà giảng viên tự chọn: users.moodle_user_id.

NULL nghĩa là giảng viên chưa liên kết (chưa bấm "Có" ở pop-up hay nút "Liên kết tài khoản Moodle"),
kể cả khi Moodle đã có tài khoản cùng email. Liên kết khóa học, tạo khóa học Moodle và đẩy lên Moodle
đều cần cột này. app_runtime đã có quyền UPDATE trên toàn bảng users nên không cần cấp thêm.

Revision ID: 0008_user_moodle_link
Revises: 0007_admin_role
Create Date: 2026-10-03

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_user_moodle_link"
down_revision: str | Sequence[str] | None = "0007_admin_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("moodle_user_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "moodle_user_id")
