"""Pending Moodle identity requests; only confirmed identities become active links."""
import sqlalchemy as sa

from alembic import op

revision = '0014_moodle_confirmation'
down_revision = '0013_local_accounts'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('moodle_request_id', sa.String(), nullable=True))
    op.add_column('users', sa.Column('moodle_request_email', sa.String(), nullable=True))
    op.create_unique_constraint('uq_users_moodle_user_id', 'users', ['moodle_user_id'])


def downgrade():
    op.drop_constraint('uq_users_moodle_user_id', 'users', type_='unique')
    op.drop_column('users', 'moodle_request_email')
    op.drop_column('users', 'moodle_request_id')
