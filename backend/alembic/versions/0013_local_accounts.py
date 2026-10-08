"""Local accounts with an admin flag and soft deletion."""
import sqlalchemy as sa

from alembic import op

revision = '0013_local_accounts'
down_revision = '0011_drop_moodle_prompted_at'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET is_admin = (role::text = 'admin')")
    op.execute("CREATE OR REPLACE FUNCTION current_user_is_admin() RETURNS boolean LANGUAGE sql STABLE AS $$ "
               "SELECT EXISTS (SELECT 1 FROM users u WHERE "
               "u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
               "AND u.is_admin AND u.is_active AND u.deleted_at IS NULL) $$")
    op.drop_column('users', 'role')
    op.execute('DROP TYPE userrole')


def downgrade():
    op.execute("CREATE TYPE userrole AS ENUM ('teacher', 'admin')")
    op.add_column('users', sa.Column('role', sa.Enum('teacher', 'admin', name='userrole', create_type=False),
                                    nullable=False, server_default='teacher'))
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin")
    op.execute("CREATE OR REPLACE FUNCTION current_user_is_admin() RETURNS boolean LANGUAGE sql STABLE AS $$ "
               "SELECT EXISTS (SELECT 1 FROM users u WHERE "
               "u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
               "AND u.role::text = 'admin' AND u.is_active) $$")
    op.drop_column('users', 'deleted_at')
    op.drop_column('users', 'is_admin')
