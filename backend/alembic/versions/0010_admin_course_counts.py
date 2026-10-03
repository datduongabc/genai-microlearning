"""Admin xem được mỗi giảng viên có bao nhiêu môn học, và chỉ con số đó.

Admin không sở hữu môn học nào nên RLS che hết bảng courses. Để xóa được một giảng viên chưa dùng
(và để trang admin biết nút Xóa nào dùng được), admin cần biết giảng viên có môn học hay không.
View này chỉ đếm theo chủ sở hữu, không có tên môn hay nội dung nào, và tự kiểm người gọi là admin
còn hoạt động (current_user_is_admin), cùng kiểu với admin_failed_jobs ở migration 0007.

Revision ID: 0010_admin_course_counts
Revises: 0009_moodle_prompted_at
Create Date: 2026-10-03

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0010_admin_course_counts"
down_revision: str | Sequence[str] | None = "0009_moodle_prompted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE VIEW admin_course_counts AS "
        "SELECT owner_id, count(*) AS course_count FROM courses "
        "WHERE current_user_is_admin() GROUP BY owner_id"
    )
    op.execute("GRANT SELECT ON admin_course_counts TO app_runtime")


def downgrade() -> None:
    op.execute("DROP VIEW admin_course_counts")
