"""More than one lecturer for a course: the course's other instructors join it as teachers.

Until now a course had one author, its owner, and a second instructor of the same LMS course was
refused at launch: whoever opened the tool first owned it, and a teaching assistant who did locked the
lecturer out. A teacher member writes what the owner writes under the course (documents, chunks,
modules, jobs, flashcard sets, videos); the course itself (rename, delete, Moodle link) stays the
owner's, and so does the daily quota, which is per account.

The courses a person may author come from a SECURITY DEFINER function, as membership does (0027), so the
policies below read course_members without its own policy reading courses back.
"""
import sqlalchemy as sa

from alembic import op

revision = "0049_course_teachers"
down_revision = "0048_erase_user"
branch_labels = None
depends_on = None

ME = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
OWNED = f"(SELECT courses.id FROM courses WHERE courses.owner_id = {ME})"
AUTHORED = "(SELECT app_authored_course_ids())"
TABLES = ("documents", "chunks", "modules", "flashcard_sets", "videos")


def _policy(table: str, name: str, using: str, command: str = "ALL") -> None:
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute(f"CREATE POLICY {name} ON {table} FOR {command} USING ({using})")


def _jobs(courses: str) -> str:
    return (f"(document_id IN (SELECT documents.id FROM documents WHERE documents.course_id IN {courses})) "
            "OR (module_id IN (SELECT modules.id FROM modules))")


def upgrade():
    op.add_column("course_members", sa.Column("role", sa.String(), server_default="learner", nullable=False))
    op.create_check_constraint("ck_course_members_role", "course_members", "role IN ('learner', 'teacher')")
    op.execute(f"""
        CREATE FUNCTION app_authored_course_ids() RETURNS SETOF uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT id FROM courses WHERE owner_id = {ME}
            UNION
            SELECT course_id FROM course_members WHERE user_id = {ME} AND role = 'teacher'
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app_authored_course_ids() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app_authored_course_ids() TO app_runtime, app_worker")
    for table in TABLES:
        _policy(table, f"{table}_tenant_isolation", f"course_id IN {AUTHORED}")
    _policy("jobs", "jobs_tenant_isolation", _jobs(AUTHORED))
    # A teacher sees who else is in the course, as the owner does.
    _policy("course_members", "course_members_read", f"user_id = {ME} OR course_id IN {AUTHORED}", "SELECT")
    # A learner who later launches as an instructor of the same course becomes its teacher: their own row only.
    op.execute("GRANT UPDATE (role) ON course_members TO app_runtime")
    op.execute(f"CREATE POLICY course_members_own_role ON course_members FOR UPDATE USING (user_id = {ME}) "
               f"WITH CHECK (user_id = {ME})")


def downgrade():
    op.execute("DROP POLICY course_members_own_role ON course_members")
    op.execute("REVOKE UPDATE (role) ON course_members FROM app_runtime")
    _policy("course_members", "course_members_read", f"user_id = {ME} OR course_id IN {OWNED}", "SELECT")
    _policy("jobs", "jobs_tenant_isolation", _jobs(OWNED))
    for table in TABLES:
        _policy(table, f"{table}_tenant_isolation", f"course_id IN {OWNED}")
    op.execute("DROP FUNCTION app_authored_course_ids()")
    op.drop_constraint("ck_course_members_role", "course_members")
    op.drop_column("course_members", "role")
