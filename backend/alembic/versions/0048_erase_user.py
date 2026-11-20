"""An administrator can erase an account's personal data (Decree 13/2023): the person asks, and their
email, name, password, Moodle link, chats, memberships, launches and logins go.

The account row stays, renamed, so ratings already given to a study keep their rater (users.id,
RESTRICT) without saying who it was. An account that still owns courses is refused: its courses
are the lecturer's teaching material, which is deleted or handed over first, not by this.

SECURITY DEFINER because row level security hides other people's chats and memberships from the
administrator, as it hides their jobs (retry_failed_job, 0007).
"""
from alembic import op

revision = "0048_erase_user"
down_revision = "0047_study_file_document"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION erase_user(p_user_id uuid) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
            v_email text;
        BEGIN
            IF NOT current_user_is_admin() THEN
                RAISE EXCEPTION 'erase_user: caller is not an administrator' USING ERRCODE = '42501';
            END IF;
            SELECT lower(email) INTO v_email FROM users WHERE id = p_user_id AND NOT is_admin FOR UPDATE;
            IF v_email IS NULL THEN
                RETURN 'not_found';
            END IF;
            IF EXISTS (SELECT 1 FROM courses WHERE owner_id = p_user_id) THEN
                RETURN 'owns_courses';
            END IF;
            DELETE FROM chat_sessions WHERE user_id = p_user_id;
            DELETE FROM course_members WHERE user_id = p_user_id;
            DELETE FROM lti_identities WHERE user_id = p_user_id;
            DELETE FROM lti_launch_codes WHERE user_id = p_user_id;
            DELETE FROM refresh_tokens WHERE user_id = p_user_id;
            UPDATE studies SET rater_emails = rater_emails - v_email, rater_pairs = rater_pairs - v_email
                WHERE rater_emails ? v_email OR rater_pairs ? v_email;
            UPDATE users SET email = 'erased-' || id || '@invalid', name = 'Tài khoản đã xóa',
                hashed_password = '!', is_active = false, moodle_user_id = NULL,
                moodle_request_id = NULL, moodle_request_email = NULL
                WHERE id = p_user_id;
            RETURN 'erased';
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION erase_user(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION erase_user(uuid) TO app_runtime")


def downgrade():
    op.execute("DROP FUNCTION erase_user(uuid)")
