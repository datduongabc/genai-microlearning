"""Thêm questions.position: thứ tự của câu hỏi trong một Quiz hay Practice.

Revision ID: 0004_question_position
Revises: 0003_user_default_model_alias
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_question_position"
down_revision: str | Sequence[str] | None = "0003_user_default_model_alias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Một Quiz giờ có nhiều câu, và các câu của một module được tạo trong cùng một transaction nên
# created_at của chúng bằng nhau: không có cột này thì thứ tự câu hỏi, và câu nào đi với giải
# thích nào, là tùy ý. Quyền và Row Level Security của questions không đổi vì cột nằm trong
# bảng đã được cấp quyền theo bảng.


def upgrade() -> None:
    # server_default 0: câu hỏi đã có trước migration là câu duy nhất của thành phần của nó.
    op.add_column("questions", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("questions", "position")
