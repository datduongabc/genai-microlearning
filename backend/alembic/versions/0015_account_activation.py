"""Replace account soft deletion with activation state alone."""
from alembic import op
import sqlalchemy as sa

revision = "0015_account_activation"
down_revision = "0014_moodle_confirmation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE OR REPLACE FUNCTION current_user_is_admin() RETURNS boolean LANGUAGE sql STABLE AS $$ "
               "SELECT EXISTS (SELECT 1 FROM users u WHERE "
               "u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
               "AND u.is_admin AND u.is_active) $$")
    op.drop_column("users", "deleted_at")


def downgrade():
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("CREATE OR REPLACE FUNCTION current_user_is_admin() RETURNS boolean LANGUAGE sql STABLE AS $$ "
               "SELECT EXISTS (SELECT 1 FROM users u WHERE "
               "u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
               "AND u.is_admin AND u.is_active AND u.deleted_at IS NULL) $$")
