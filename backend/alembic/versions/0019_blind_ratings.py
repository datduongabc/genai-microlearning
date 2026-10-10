"""Email invitations, guarded blind projections, and own human ratings."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_blind_ratings"
down_revision = "0018_study_model"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("studies", sa.Column("rater_emails", postgresql.JSONB(), nullable=False,
                                      server_default="[]"))
    # Source tables stay admin-only: RLS cannot hide individual group/module columns.
    op.execute("""
        CREATE FUNCTION study_invited(p_study_id uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (
            SELECT 1 FROM studies s JOIN users u
            ON u.id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            WHERE s.id = p_study_id AND u.is_active
              AND s.rater_emails ? lower(u.email)
        ) $$
    """)
    op.execute("REVOKE ALL ON FUNCTION study_invited(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION study_invited(uuid) TO app_runtime")
    op.execute("""
        CREATE VIEW blind_studies WITH (security_barrier = true) AS
        SELECT id, name, rubric_version, status, created_at
        FROM studies WHERE study_invited(id)
    """)
    op.execute("""
        CREATE VIEW blind_study_items WITH (security_barrier = true) AS
        SELECT id, study_id, anon_code, packet FROM study_items WHERE study_invited(study_id)
    """)
    op.execute("GRANT SELECT ON blind_studies, blind_study_items TO app_runtime")
    op.execute("""
        CREATE FUNCTION rating_item_invited(p_item_id uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT EXISTS (SELECT 1 FROM study_items WHERE id = p_item_id AND study_invited(study_id))
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION rating_item_invited(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION rating_item_invited(uuid) TO app_runtime")
    own = """rater_kind = 'human'
        AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        AND rating_item_invited(study_item_id)"""
    op.execute(f"CREATE POLICY ratings_own_read ON ratings FOR SELECT TO app_runtime USING ({own})")
    op.execute(f"CREATE POLICY ratings_own_insert ON ratings FOR INSERT TO app_runtime WITH CHECK ({own})")
    op.execute(f"CREATE POLICY ratings_own_update ON ratings FOR UPDATE TO app_runtime "
               f"USING ({own}) WITH CHECK ({own})")
    # Takes the study lock that loading and invitation edits take, so a rating cannot slip in
    # while items are being loaded and their codes reassigned.
    op.execute("""
        CREATE FUNCTION guard_rating_write() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE sid uuid; study_state text;
        BEGIN
            IF TG_OP = 'UPDATE' AND (NEW.study_item_id IS DISTINCT FROM OLD.study_item_id
                OR NEW.user_id IS DISTINCT FROM OLD.user_id
                OR NEW.rater_kind IS DISTINCT FROM OLD.rater_kind) THEN
                RAISE EXCEPTION 'Rating identity cannot change' USING ERRCODE = '23514';
            END IF;
            SELECT study_id INTO sid FROM study_items WHERE id = NEW.study_item_id;
            SELECT status INTO study_state FROM studies WHERE id = sid FOR UPDATE;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = session_user AND rolsuper)
                AND current_user_is_admin() IS NOT TRUE
                AND rating_item_invited(NEW.study_item_id) IS NOT TRUE THEN
                RAISE EXCEPTION 'Study not found' USING ERRCODE = '42501';
            END IF;
            IF study_state = 'closed' THEN
                RAISE EXCEPTION 'Study is closed' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION guard_rating_write() FROM PUBLIC")
    op.execute("CREATE TRIGGER ratings_guard_write BEFORE INSERT OR UPDATE ON ratings "
               "FOR EACH ROW EXECUTE FUNCTION guard_rating_write()")


def downgrade():
    op.execute("DROP TRIGGER ratings_guard_write ON ratings")
    op.execute("DROP FUNCTION guard_rating_write()")
    for action in ("read", "insert", "update"):
        op.execute(f"DROP POLICY ratings_own_{action} ON ratings")
    op.execute("DROP FUNCTION rating_item_invited(uuid)")
    op.execute("DROP VIEW blind_study_items, blind_studies")
    op.execute("DROP FUNCTION study_invited(uuid)")
    op.drop_column("studies", "rater_emails")
