"""Link edit conversations to modules and record applied turns."""
import sqlalchemy as sa

from alembic import op

revision = "0006_module_chat_edits"
down_revision = "0005_clear_old_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("module_id", sa.UUID(), nullable=True))
    op.create_foreign_key("chat_sessions_module_id_fkey", "chat_sessions", "modules", ["module_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_chat_sessions_module_id", "chat_sessions", ["module_id"])
    op.add_column("chat_messages", sa.Column("applied_edit", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("chat_messages", "applied_edit")
    op.drop_index("ix_chat_sessions_module_id", table_name="chat_sessions")
    op.drop_constraint("chat_sessions_module_id_fkey", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "module_id")
