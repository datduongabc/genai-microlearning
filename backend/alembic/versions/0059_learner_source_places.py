"""Published flashcard sets and videos keep their document's name and source labels for learners, whatever the
document's learner access.

A lecturer may publish flashcards made from a document they keep closed to the chat (0057). The learner still
reads the set, so they need its document's name and where each card comes from ("trang 9"), but not the
passages: the chunks of a closed document stay hidden, so the chat cannot find them. The document row is
readable when it is open or has published material; the labels come from a function that returns only the
place of chunks of such a document, never their text.
"""
from alembic import op

revision = "0059_learner_source_places"
down_revision = "0058_chat_trace_and_feedback"
branch_labels = None
depends_on = None

MEMBER = "(SELECT app_member_course_ids())"
PUBLISHED = ("(EXISTS (SELECT 1 FROM flashcard_sets f WHERE f.document_id = {d}.id AND f.published_at IS NOT NULL)"
             " OR EXISTS (SELECT 1 FROM videos v WHERE v.document_id = {d}.id AND v.published_at IS NOT NULL))")


def upgrade():
    op.execute("DROP POLICY documents_member_read ON documents")
    op.execute("CREATE POLICY documents_member_read ON documents FOR SELECT USING "
               f"(course_id IN {MEMBER} AND (learner_access OR {PUBLISHED.format(d='documents')}))")
    op.execute(f"""
        CREATE FUNCTION learner_chunk_places(document uuid, ids uuid[])
        RETURNS TABLE (id uuid, source_ref text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT c.id, c.source_ref FROM chunks c JOIN documents d ON d.id = c.document_id
             WHERE c.document_id = document AND c.id = ANY(ids)
               AND d.course_id IN {MEMBER}
               AND (d.learner_access OR {PUBLISHED.format(d='d')})
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION learner_chunk_places(uuid, uuid[]) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION learner_chunk_places(uuid, uuid[]) TO app_runtime")


def downgrade():
    op.execute("DROP FUNCTION learner_chunk_places(uuid, uuid[])")
    op.execute("DROP POLICY documents_member_read ON documents")
    op.execute(f"CREATE POLICY documents_member_read ON documents FOR SELECT USING (course_id IN {MEMBER} AND learner_access)")
