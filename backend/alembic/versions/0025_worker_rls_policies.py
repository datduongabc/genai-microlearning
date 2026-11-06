"""Exempt the worker from row level security by policy instead of BYPASSRLS.

Postgres 16 lets only a role that has BYPASSRLS give or take it, and the master user of RDS is not
a superuser, so 0001 could not give it there and the first migration on RDS failed. A policy for
app_worker that passes every row does the same on every table that has row level security, and
needs only the table owner. Where this migration can, it also takes BYPASSRLS away, so that local,
CI and RDS all exempt the worker the same way and a test of one is a test of all.

The worker stays limited by its per-table grants (0001): a policy cannot let it read a table it
has no grant on. A table that gets row level security later needs the same policy, see
tests/integration/test_worker_role.py.
"""

from alembic import op

revision = "0025_worker_rls_policies"
down_revision = "0024_cross_family_judges"
branch_labels = None
depends_on = None

POLICY = "worker_processes_every_tenant"


def upgrade():
    op.execute(f"""
        DO $$
        DECLARE t record;
        BEGIN
            FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity LOOP
                EXECUTE format('DROP POLICY IF EXISTS {POLICY} ON %I', t.relname);
                EXECUTE format('CREATE POLICY {POLICY} ON %I TO app_worker USING (true) WITH CHECK (true)', t.relname);
            END LOOP;
            IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user) THEN
                ALTER ROLE app_worker NOBYPASSRLS;
            END IF;
        END $$;
    """)


def downgrade():
    op.execute(f"""
        DO $$
        DECLARE t record;
        BEGIN
            IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user) THEN
                ALTER ROLE app_worker BYPASSRLS;
            ELSE
                RAISE EXCEPTION 'downgrading would leave the worker under row level security: '
                                'BYPASSRLS needs a role that has it';
            END IF;
            FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public' AND c.relkind = 'r' LOOP
                EXECUTE format('DROP POLICY IF EXISTS {POLICY} ON %I', t.relname);
            END LOOP;
        END $$;
    """)
