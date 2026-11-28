"""What happened in each chat answer, and what the reader said about it.

`trace` keeps, for an answer, how it was made: the route (fixed reply, saved answer, refused for want of a
relevant passage, answered), the question as rewritten for the search, the passages with their reranker scores,
the seconds of each step and the tokens of each model call. Module generation keeps its timings; the chat,
which people wait on, kept none.

`feedback` is the reader's own verdict on an answer: helpful, not right, or a wrong citation (with the numbers
of the citations they flagged and an optional note). `opened_citations` is what they did without saying so:
which citations they opened.

A lecturer reads no one's chat (0027). They read the answers that were given feedback, without the asker's name,
through `course_chat_feedback`, which returns rows only for a course the caller authors. The reader is told
before sending feedback that it goes to the lecturer anonymously.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0058_chat_trace_and_feedback"
down_revision = "0057_document_learner_access"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_messages", sa.Column("trace", JSONB(), nullable=True))
    op.add_column("chat_messages", sa.Column("feedback", sa.String(), nullable=True))
    op.create_check_constraint("ck_chat_messages_feedback", "chat_messages",
                               "feedback IN ('helpful', 'not_right', 'wrong_citation')")
    op.add_column("chat_messages", sa.Column("feedback_note", sa.Text(), nullable=True))
    op.add_column("chat_messages", sa.Column("feedback_citations", JSONB(), server_default="[]", nullable=False))
    op.add_column("chat_messages", sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_messages", sa.Column("opened_citations", JSONB(), server_default="[]", nullable=False))
    op.create_index("ix_chat_messages_feedback", "chat_messages", ["feedback"],
                    postgresql_where=sa.text("feedback IS NOT NULL"))

    op.execute("""
        CREATE FUNCTION course_chat_feedback(course uuid)
        RETURNS TABLE (message_id uuid, answered_at timestamptz, question text, answer text, citations jsonb,
                       feedback text, note text, flagged_citations jsonb, feedback_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT m.id, m.created_at,
                   (SELECT q.content FROM chat_messages q
                     WHERE q.session_id = m.session_id AND q.role = 'user' AND q.created_at <= m.created_at
                     ORDER BY q.created_at DESC LIMIT 1),
                   m.content,
                   (SELECT coalesce(jsonb_agg(jsonb_build_object('chunk_id', c->>'chunk_id', 'source_ref', c->>'source_ref')), '[]')
                      FROM jsonb_array_elements(m.citations) c),
                   m.feedback, m.feedback_note, m.feedback_citations, m.feedback_at
              FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
             WHERE s.course_id = course AND m.feedback IS NOT NULL
               AND course IN (SELECT app_authored_course_ids())
             ORDER BY m.feedback_at DESC
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION course_chat_feedback(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION course_chat_feedback(uuid) TO app_runtime")


def downgrade():
    op.execute("DROP FUNCTION course_chat_feedback(uuid)")
    op.drop_index("ix_chat_messages_feedback", table_name="chat_messages")
    for column in ("opened_citations", "feedback_at", "feedback_citations", "feedback_note"):
        op.drop_column("chat_messages", column)
    op.drop_constraint("ck_chat_messages_feedback", "chat_messages")
    op.drop_column("chat_messages", "feedback")
    op.drop_column("chat_messages", "trace")
