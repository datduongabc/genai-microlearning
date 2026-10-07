"""users.hashed_password có thể trống: mật khẩu của giảng viên nằm ở Moodle.

Giảng viên đăng nhập bằng tài khoản Moodle (app/auth/moodle_login.py), nên ứng dụng không giữ mật khẩu
nào cho họ: cột này chỉ còn dùng cho admin, tài khoản cục bộ do scripts/create_admin.py tạo. Migration
cho phép NULL và xóa các hash cũ của giảng viên (không còn dùng để đăng nhập). Downgrade đặt lại một giá
trị không phải hash bcrypt hợp lệ ("!") cho các dòng trống, nên không ai đăng nhập được bằng chúng, rồi
đặt lại NOT NULL.

Revision ID: 0012_lecturer_password_nullable
Revises: 0011_drop_moodle_prompted_at
Create Date: 2026-10-07

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_lecturer_password_nullable"
down_revision: str | Sequence[str] | None = "0011_drop_moodle_prompted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "hashed_password", existing_type=sa.String(), nullable=True)
    op.execute("UPDATE users SET hashed_password = NULL WHERE role::text = 'teacher'")


def downgrade() -> None:
    op.execute("UPDATE users SET hashed_password = '!' WHERE hashed_password IS NULL")
    op.alter_column("users", "hashed_password", existing_type=sa.String(), nullable=False)
