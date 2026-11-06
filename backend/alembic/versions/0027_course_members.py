"""Students in a course: course membership, read-only to them, and chats of one's own.

Until now a course was visible only to its owner, and every table under it was filtered by "the course is
visible". With members that is no longer the same as "the course is mine": a member must read the course's
documents and chunks to chat with them, and nothing else. So the policies are split:

- courses: the owner's policy stays; members get a SELECT-only policy.
- documents, chunks: the owner may do anything; members may SELECT.
- modules and jobs: the owner only. Their children (components, questions, evaluations) follow modules.
- chat_sessions: one's own sessions in a course one can see. A chat belongs to the person who had it, so the
  lecturer does not read the students' chats; existing sessions become the course owner's, which they were.

Membership is listed by a SECURITY DEFINER function, so that the courses policy can read course_members
without course_members' own policy reading courses back (Postgres refuses that recursion).

A member can only be added by the member themself, to a course an LMS context is linked to: an LTI launch.
"""
import sqlalchemy as sa

from alembic import op

revision = "0027_course_members"
down_revision = "0026_lti"
branch_labels = None
depends_on = None

ME = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
OWNED = f"(SELECT courses.id FROM courses WHERE courses.owner_id = {ME})"
MEMBER = "(SELECT app_member_course_ids())"


def _policy(table: str, name: str, using: str, command: str = "ALL") -> None:
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute(f"CREATE POLICY {name} ON {table} FOR {command} USING ({using})")


def upgrade():
    op.add_column("users", sa.Column("can_author", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("users", sa.Column("chat_day", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("chat_count", sa.Integer(), server_default="0", nullable=False))

    op.create_table(
        "course_members",
        sa.Column("course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source", sa.String(), server_default="lti", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_course_members_user_id", "course_members", ["user_id"])
    op.execute("GRANT SELECT, INSERT ON course_members TO app_runtime")

    op.execute(f"""
        CREATE FUNCTION app_member_course_ids() RETURNS SETOF uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT course_id FROM course_members WHERE user_id = {ME}
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app_member_course_ids() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app_member_course_ids() TO app_runtime, app_worker")

    op.execute("ALTER TABLE course_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY course_members_read ON course_members FOR SELECT "
        f"USING (user_id = {ME} OR course_id IN {OWNED})"
    )
    op.execute(
        "CREATE POLICY course_members_join ON course_members FOR INSERT "
        f"WITH CHECK (user_id = {ME} AND course_id IN (SELECT lti_contexts.course_id FROM lti_contexts))"
    )

    # As every table under row level security (migration 0025); the worker has no grant on it anyway.
    op.execute("CREATE POLICY worker_processes_every_tenant ON course_members TO app_worker USING (true) WITH CHECK (true)")

    op.execute(f"CREATE POLICY courses_member_read ON courses FOR SELECT USING (id IN {MEMBER})")

    for table in ("documents", "chunks", "modules"):
        _policy(table, f"{table}_tenant_isolation", f"course_id IN {OWNED}")
    for table in ("documents", "chunks"):
        _policy(table, f"{table}_member_read", f"course_id IN {MEMBER}", "SELECT")
    _policy("jobs", "jobs_tenant_isolation",
            f"(document_id IN (SELECT documents.id FROM documents WHERE documents.course_id IN {OWNED})) "
            "OR (module_id IN (SELECT modules.id FROM modules))")

    op.add_column("chat_sessions", sa.Column("user_id", sa.UUID(), nullable=True))
    op.execute("UPDATE chat_sessions SET user_id = courses.owner_id FROM courses WHERE courses.id = chat_sessions.course_id")
    op.alter_column("chat_sessions", "user_id", nullable=False)
    op.create_foreign_key("chat_sessions_user_id_fkey", "chat_sessions", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    _policy("chat_sessions", "chat_sessions_tenant_isolation",
            f"user_id = {ME} AND course_id IN (SELECT courses.id FROM courses)")


def downgrade():
    visible = "(SELECT courses.id FROM courses)"
    _policy("chat_sessions", "chat_sessions_tenant_isolation", f"course_id IN {visible}")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_constraint("chat_sessions_user_id_fkey", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "user_id")

    _policy("jobs", "jobs_tenant_isolation",
            "(document_id IN (SELECT documents.id FROM documents)) OR (module_id IN (SELECT modules.id FROM modules))")
    for table in ("documents", "chunks"):
        op.execute(f"DROP POLICY IF EXISTS {table}_member_read ON {table}")
    for table in ("documents", "chunks", "modules"):
        _policy(table, f"{table}_tenant_isolation", f"course_id IN {visible}")
    op.execute("DROP POLICY IF EXISTS courses_member_read ON courses")

    op.drop_table("course_members")
    op.execute("DROP FUNCTION app_member_course_ids()")
    op.drop_column("users", "chat_count")
    op.drop_column("users", "chat_day")
    op.drop_column("users", "can_author")
