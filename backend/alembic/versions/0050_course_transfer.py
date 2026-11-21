"""An administrator hands a course over to another lecturer.

A lecturer who leaves, or who asks for their data to be erased (0048, refused while they own courses),
would otherwise take the teaching material with them: everything under a course (documents, modules,
flashcard sets, videos, its Moodle link) hangs off courses.owner_id alone, so changing it hands all of it
over. The previous owner may stay as a teacher of the course (0049) or leave it.

SECURITY DEFINER because row level security hides other lecturers' courses from the administrator, as
erase_user does.
"""
from alembic import op

revision = "0050_course_transfer"
down_revision = "0049_course_teachers"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION admin_user_courses(p_user_id uuid)
        RETURNS TABLE (id uuid, name text, moodle_course_id integer, created_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT c.id, c.name::text, c.moodle_course_id, c.created_at FROM courses c
            WHERE c.owner_id = p_user_id AND current_user_is_admin() ORDER BY c.created_at
        $$
    """)
    op.execute("""
        CREATE FUNCTION transfer_course(p_course_id uuid, p_new_owner_id uuid, p_keep_previous boolean)
        RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
            v_previous uuid;
        BEGIN
            IF NOT current_user_is_admin() THEN
                RAISE EXCEPTION 'transfer_course: caller is not an administrator' USING ERRCODE = '42501';
            END IF;
            SELECT owner_id INTO v_previous FROM courses WHERE id = p_course_id FOR UPDATE;
            IF v_previous IS NULL THEN
                RETURN 'course_not_found';
            END IF;
            -- Someone who can author and log in: not a disabled, student-only or erased account.
            IF NOT EXISTS (SELECT 1 FROM users WHERE id = p_new_owner_id AND is_active AND can_author) THEN
                RETURN 'owner_not_eligible';
            END IF;
            IF v_previous = p_new_owner_id THEN
                RETURN 'same_owner';
            END IF;
            UPDATE courses SET owner_id = p_new_owner_id WHERE id = p_course_id;
            -- The new owner was perhaps a teacher or learner of it; an owner is not also a member.
            DELETE FROM course_members WHERE course_id = p_course_id AND user_id = p_new_owner_id;
            IF p_keep_previous THEN
                INSERT INTO course_members (course_id, user_id, source, role)
                VALUES (p_course_id, v_previous, 'transfer', 'teacher')
                ON CONFLICT (course_id, user_id) DO UPDATE SET role = 'teacher';
            END IF;
            RETURN 'transferred';
        END
        $$
    """)
    for fn in ("admin_user_courses(uuid)", "transfer_course(uuid, uuid, boolean)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO app_runtime")


def downgrade():
    op.execute("DROP FUNCTION transfer_course(uuid, uuid, boolean)")
    op.execute("DROP FUNCTION admin_user_courses(uuid)")
