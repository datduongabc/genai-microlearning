"""Bỏ users.moodle_prompted_at: pop-up liên kết Moodle không còn.

Pop-up đã bị gỡ; việc liên kết sau này sẽ được xác nhận ngay trong Moodle, nên không còn gì để nhớ
về việc đã trả lời pop-up hay chưa. Các liên kết đã lưu (users.moodle_user_id)
giữ nguyên. Downgrade thêm lại cột, để trống.

Revision ID: 0011_drop_moodle_prompted_at
Revises: 0010_admin_course_counts
Create Date: 2026-10-07

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_drop_moodle_prompted_at"
down_revision: str | Sequence[str] | None = "0010_admin_course_counts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "moodle_prompted_at")


def downgrade() -> None:
    op.add_column("users", sa.Column("moodle_prompted_at", sa.DateTime(timezone=True), nullable=True))
