"""A rater rates only the pairs given to them: invited and not given any yet, they see none.

0044 let a rater without an entry rate every pair. The admin then had to untick pairs for each
new rater, and a pair added later reached every such rater unasked. Now the assignment table
starts empty and a tick is the only way a rater gets a pair.
"""
from alembic import op

revision = "0045_rater_pairs_explicit"
down_revision = "0044_rater_pairs"
branch_labels = None
depends_on = None


def _assigned(rule: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION study_item_assigned(p_study_id uuid, p_key text) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (
            SELECT 1 FROM studies s JOIN users u
            ON u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            WHERE s.id = p_study_id AND u.is_active
              AND s.rater_emails ? lower(u.email)
              AND {rule}
        ) $$
    """


def upgrade():
    op.execute(_assigned("coalesce(s.rater_pairs -> lower(u.email) ? p_key, false)"))


def downgrade():
    op.execute(_assigned("(NOT s.rater_pairs ? lower(u.email) OR s.rater_pairs -> lower(u.email) ? p_key)"))
