"""Bỏ giá trị student khỏi enum userrole.

PostgreSQL không có lệnh bỏ một giá trị khỏi enum, nên phải dựng lại cả kiểu.

Revision ID: 0002_drop_student_role
Revises: 0001_initial_schema
Create Date: 2026-09-23

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_drop_student_role"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dừng lại thay vì tự đổi dữ liệu: nếu thật sự có tài khoản student thì đó là
    # điều bất ngờ, và đổi thầm họ thành giảng viên là trao quyền cho người lạ.
    con_lai = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'student'")
    ).scalar()
    if con_lai:
        raise RuntimeError(
            f"Còn {con_lai} tài khoản mang vai trò student. Hãy xử lý chúng trước khi bỏ vai trò này."
        )

    op.execute("ALTER TYPE userrole RENAME TO userrole_cu")
    op.execute("CREATE TYPE userrole AS ENUM ('teacher')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_cu")


def downgrade() -> None:
    op.execute("ALTER TYPE userrole RENAME TO userrole_cu")
    op.execute("CREATE TYPE userrole AS ENUM ('teacher', 'student')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_cu")
