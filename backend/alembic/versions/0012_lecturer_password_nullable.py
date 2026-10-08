"""Allow absent hashes on imported accounts; normal local creation always hashes a password."""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_lecturer_password_nullable"
down_revision: str | Sequence[str] | None = "0011_drop_moodle_prompted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "hashed_password", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE users SET hashed_password = '!' WHERE hashed_password IS NULL")
    op.alter_column("users", "hashed_password", existing_type=sa.String(), nullable=False)
