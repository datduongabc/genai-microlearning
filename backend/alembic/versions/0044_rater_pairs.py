"""Each rater may be given only some pairs of a study.

studies.rater_pairs maps a rater's email to the pair keys they rate (a pair's id, or a human
packet's own id). A rater without an entry rates every pair, as before. The database applies it,
in the views and the rating policy a rater goes through, so a packet not given to a rater is not
readable by them at all.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0044_rater_pairs"
down_revision = "0043_imported_study_pairs"
branch_labels = None
depends_on = None

KEY = "COALESCE(pair_id, id)::text"


def upgrade():
    op.add_column("studies", sa.Column("rater_pairs", JSONB, nullable=False, server_default="{}"))
    op.execute("""
        CREATE FUNCTION study_item_assigned(p_study_id uuid, p_key text) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (
            SELECT 1 FROM studies s JOIN users u
            ON u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            WHERE s.id = p_study_id AND u.is_active
              AND s.rater_emails ? lower(u.email)
              AND (NOT s.rater_pairs ? lower(u.email) OR s.rater_pairs -> lower(u.email) ? p_key)
        ) $$
    """)
    op.execute("REVOKE ALL ON FUNCTION study_item_assigned(uuid, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION study_item_assigned(uuid, text) TO app_runtime")
    op.execute(f"""
        CREATE OR REPLACE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet, reference_file_id FROM study_items
        WHERE study_item_assigned(study_id, {KEY})
    """)
    op.execute("""
        CREATE OR REPLACE VIEW blind_study_files WITH (security_barrier = true) AS
        SELECT f.id, f.study_id, f.filename, f.size FROM study_files f
        WHERE f.status = 'ready' AND study_invited(f.study_id)
          AND EXISTS (SELECT 1 FROM study_items i WHERE i.reference_file_id = f.id
                      AND study_item_assigned(i.study_id, COALESCE(i.pair_id, i.id)::text))
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION rating_item_invited(p_item_id uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (SELECT 1 FROM study_items WHERE id = p_item_id AND study_item_assigned(study_id, {KEY}))
        $$
    """)


def downgrade():
    op.execute("""
        CREATE OR REPLACE FUNCTION rating_item_invited(p_item_id uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (SELECT 1 FROM study_items WHERE id = p_item_id AND study_invited(study_id))
        $$
    """)
    op.execute("""
        CREATE OR REPLACE VIEW blind_study_files WITH (security_barrier = true) AS
        SELECT f.id, f.study_id, f.filename, f.size FROM study_files f
        WHERE f.status = 'ready' AND study_invited(f.study_id)
          AND EXISTS (SELECT 1 FROM study_items i WHERE i.reference_file_id = f.id)
    """)
    op.execute("""
        CREATE OR REPLACE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet, reference_file_id FROM study_items WHERE study_invited(study_id)
    """)
    op.execute("DROP FUNCTION study_item_assigned(uuid, text)")
    op.drop_column("studies", "rater_pairs")
