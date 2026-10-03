"""Vai trò admin, khóa tài khoản, quyền đọc lỗi và nút Retry.

- userrole thêm giá trị admin, users thêm is_active.
- Admin không thuộc môn học nào nên RLS vẫn che hết dữ liệu của giảng viên, và bảng jobs, documents,
  courses không được thêm policy nào. Admin chỉ đi qua hai cửa hẹp, cả hai tự kiểm người gọi là
  admin còn hoạt động (current_user_is_admin):
  * view admin_failed_jobs: job đang lỗi, kèm tên tệp, tên môn học và giảng viên sở hữu, đúng các
    cột đó, không có payload hay nội dung tài liệu;
  * hàm retry_failed_job: đẩy một job đang lỗi về queued. Giảng viên cũng dùng đúng hàm này cho job
    của chính mình, nên hai nút Retry không thể lệch nhau.

Revision ID: 0007_admin_role
Revises: 0006_module_chat_edits
Create Date: 2026-10-03

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_admin_role"
down_revision: str | Sequence[str] | None = "0006_module_chat_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Người gọi (theo app.current_user_id mà get_current_user đặt cho mỗi request) có phải admin còn
# hoạt động không. Đọc bảng users, không có RLS, nên không cần quyền đặc biệt.
_IS_ADMIN = "current_user_is_admin()"


def upgrade() -> None:
    # Giá trị enum mới chỉ dùng được sau khi giao dịch thêm nó đã commit.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'admin'")

    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.execute(
        "CREATE FUNCTION current_user_is_admin() RETURNS boolean LANGUAGE sql STABLE AS $$ "
        "SELECT EXISTS (SELECT 1 FROM users u "
        "WHERE u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND u.role::text = 'admin' AND u.is_active) $$"
    )

    # Job lỗi mà sau đó đã có job mới cùng loại, cùng tài liệu, cùng mục (giảng viên đã sinh lại)
    # không còn là lỗi cần xử lý: không hiện và không cho retry. View này chỉ dùng nội bộ (không
    # cấp quyền cho app_runtime), hai cửa bên dưới đọc nó với quyền của chủ.
    op.execute(
        "CREATE VIEW open_failed_jobs AS "
        "SELECT j.id, j.job_type, j.step, j.attempts, j.max_attempts, j.last_error, j.created_at, "
        "j.module_id, j.document_id, d.filename AS document_filename, c.name AS course_name, "
        "c.owner_id, u.name AS owner_name, u.email AS owner_email "
        "FROM jobs j JOIN documents d ON d.id = j.document_id "
        "JOIN courses c ON c.id = d.course_id JOIN users u ON u.id = c.owner_id "
        "WHERE j.status = 'failed' "
        "AND NOT EXISTS (SELECT 1 FROM jobs n WHERE n.document_id = j.document_id "
        "AND n.job_type = j.job_type AND n.section_id IS NOT DISTINCT FROM j.section_id "
        "AND n.created_at > j.created_at)"
    )
    op.execute(
        "CREATE VIEW admin_failed_jobs AS "
        "SELECT id, job_type, step, attempts, max_attempts, last_error, created_at, module_id, "
        "document_filename, course_name, owner_name, owner_email "
        f"FROM open_failed_jobs WHERE {_IS_ADMIN}"
    )
    op.execute("GRANT SELECT ON admin_failed_jobs TO app_runtime")

    # SECURITY DEFINER: chạy với quyền của chủ để đẩy được job của người khác về queued, nhưng chỉ
    # khi người gọi là admin hoặc chủ của môn học chứa job đó. Tài liệu đang xử lý lại thì hiện
    # "processing" (worker cũng đặt lại khi nhận job). search_path cố định để không ai chèn được
    # hàm hay bảng cùng tên.
    op.execute(
        "CREATE FUNCTION retry_failed_job(p_job_id uuid) RETURNS boolean LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path = public, pg_temp AS $$ "
        "DECLARE v_document uuid; v_type jobtype; "
        "v_caller uuid := NULLIF(current_setting('app.current_user_id', true), '')::uuid; "
        "BEGIN "
        "SELECT f.document_id, f.job_type INTO v_document, v_type FROM open_failed_jobs f "
        f"WHERE f.id = p_job_id AND ({_IS_ADMIN} OR f.owner_id = v_caller); "
        "IF NOT FOUND THEN RETURN false; END IF; "
        "UPDATE jobs SET status = 'queued', attempts = 0, run_after = now(), locked_at = NULL, "
        "locked_by = NULL WHERE id = p_job_id AND status = 'failed'; "
        "IF NOT FOUND THEN RETURN false; END IF; "
        "IF v_type = 'document_pipeline' THEN "
        "UPDATE documents SET status = 'processing' WHERE id = v_document AND status = 'failed'; "
        "END IF; "
        "RETURN true; END $$"
    )
    op.execute("REVOKE ALL ON FUNCTION retry_failed_job(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION retry_failed_job(uuid) TO app_runtime")


def downgrade() -> None:
    con_lai = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE role::text = 'admin'")).scalar()
    if con_lai:
        raise RuntimeError(f"Còn {con_lai} tài khoản admin. Hãy xử lý chúng trước khi bỏ vai trò này.")

    op.execute("DROP FUNCTION retry_failed_job(uuid)")
    op.execute("DROP VIEW admin_failed_jobs")
    op.execute("DROP VIEW open_failed_jobs")
    op.execute("DROP FUNCTION current_user_is_admin()")
    op.drop_column("users", "is_active")

    op.execute("ALTER TYPE userrole RENAME TO userrole_cu")
    op.execute("CREATE TYPE userrole AS ENUM ('teacher')")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole")
    op.execute("DROP TYPE userrole_cu")
