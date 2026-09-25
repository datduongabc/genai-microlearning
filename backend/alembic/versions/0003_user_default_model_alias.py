"""Thêm users.default_model_alias: model giảng viên chọn sẵn cho các lượt sinh.

Revision ID: 0003_user_default_model_alias
Revises: 0002_drop_student_role
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_user_default_model_alias"
down_revision: str | Sequence[str] | None = "0002_drop_student_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Cột này nằm trong users, mà app_runtime đã có quyền UPDATE trên toàn bảng users từ
# migration đầu (GRANT theo bảng, không theo cột), nên không cần cấp thêm quyền gì.
# Row Level Security của users cũng không đổi.


def upgrade() -> None:
    # nullable: NULL nghĩa là giảng viên chưa chọn, khi đó dùng model trong cấu hình.
    # Không đặt giá trị mặc định ở tầng database: mặc định của hệ thống nằm ở
    # settings.llm_model, để hai chỗ cùng giữ một mặc định là mời gọi chúng lệch nhau.
    op.add_column("users", sa.Column("default_model_alias", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "default_model_alias")
