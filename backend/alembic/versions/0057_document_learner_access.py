"""Which documents a course's learners may ask about, and a fingerprint of each document's file.

Until now a learner member read every document and chunk of the course (migration 0027), so the course chat
answered a learner from anything the lecturer uploaded, an answer key or internal notes included, and showed
the passage itself under the citation. Now a document is open to learners only when an author says so:
`learner_access`. Documents that exist keep answering learners, as they did; a new document starts closed.
The database enforces it, so a learner's search cannot find a closed document whatever the code does.

`content_sha256` lets the worker refuse a second copy of a file already in the course: two copies of each
passage would push other passages out of every search.
"""
import sqlalchemy as sa

from alembic import op

revision = "0057_document_learner_access"
down_revision = "0056_module_component_version"
branch_labels = None
depends_on = None

MEMBER = "(SELECT app_member_course_ids())"


def _policy(table: str, name: str, using: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute(f"CREATE POLICY {name} ON {table} FOR SELECT USING ({using})")


def upgrade():
    op.add_column("documents", sa.Column("learner_access", sa.Boolean(), server_default="false", nullable=False))
    op.execute("UPDATE documents SET learner_access = true")
    op.add_column("documents", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_index("ix_documents_course_id_content_sha256", "documents", ["course_id", "content_sha256"])
    # Authors keep their own policies (0049); these only narrow what a member who is not an author reads.
    _policy("documents", "documents_member_read", f"course_id IN {MEMBER} AND learner_access")
    _policy("chunks", "chunks_member_read",
            f"course_id IN {MEMBER} AND document_id IN (SELECT documents.id FROM documents WHERE documents.learner_access)")


def downgrade():
    _policy("chunks", "chunks_member_read", f"course_id IN {MEMBER}")
    _policy("documents", "documents_member_read", f"course_id IN {MEMBER}")
    op.drop_index("ix_documents_course_id_content_sha256", table_name="documents")
    op.drop_column("documents", "content_sha256")
    op.drop_column("documents", "learner_access")
