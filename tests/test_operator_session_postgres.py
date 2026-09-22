"""Session lookup ACLs and revocation semantics on disposable PostgreSQL only."""
import os
import unittest
from pathlib import Path
import psycopg2
from psycopg2.extensions import parse_dsn

DSN = os.getenv('POLK_TEST_DB_URL')
MIGRATION = Path(__file__).resolve().parents[1] / 'supabase/migrations/20260922134200_operator_active_session.sql'
USER = '11111111-1111-4111-8111-111111111111'
SESSION = '33333333-3333-4333-8333-333333333333'
OTHER = '22222222-2222-4222-8222-222222222222'


@unittest.skipUnless(DSN, 'requires disposable localhost PostgreSQL')
class OperatorSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        p = parse_dsn(DSN)
        if p.get('host') not in ('localhost', '127.0.0.1') or p.get('port') != '55432' or p.get('dbname') != 'postgres' or p.get('hostaddr'):
            raise RuntimeError('Disposable database required')
        self.db = psycopg2.connect(DSN)
        self.addCleanup(self.db.close)
        with self.db, self.db.cursor() as c:
            c.execute('DROP FUNCTION IF EXISTS public.god_mode_operator_session_active(uuid,uuid); DROP SCHEMA IF EXISTS auth CASCADE; CREATE SCHEMA auth')
            c.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF; END $$")
            c.execute('CREATE TABLE auth.users(id uuid PRIMARY KEY,email_confirmed_at timestamptz,is_anonymous boolean,deleted_at timestamptz,banned_until timestamptz); CREATE TABLE auth.sessions(id uuid PRIMARY KEY,user_id uuid REFERENCES auth.users(id),not_after timestamptz)')
            c.execute('INSERT INTO auth.users VALUES(%s,now(),false,NULL,NULL); INSERT INTO auth.sessions VALUES(%s,%s,NULL)', (USER, SESSION, USER))
            c.execute(MIGRATION.read_text(encoding='utf-8'))

    def q(self, sql, args=None):
        with self.db, self.db.cursor() as c:
            c.execute(sql, args)
            return c.fetchone() if c.description else None

    def active(self, user=USER, session=SESSION):
        with self.db, self.db.cursor() as c:
            c.execute('SET LOCAL ROLE service_role')
            c.execute('SELECT public.god_mode_operator_session_active(%s,%s)', (user, session))
            return c.fetchone()[0]

    def test_only_service_can_execute_without_exposing_auth_tables(self):
        self.assertTrue(self.active())
        for role in ('anon', 'authenticated'):
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                with self.db, self.db.cursor() as c:
                    c.execute('SET LOCAL ROLE ' + role)
                    c.execute('SELECT public.god_mode_operator_session_active(%s,%s)', (USER, SESSION))
        for role in ('anon', 'authenticated', 'service_role'):
            self.assertEqual(self.q("SELECT has_table_privilege(%s,'auth.sessions','SELECT'),has_table_privilege(%s,'auth.users','SELECT')", (role, role)), (False, False))

    def test_session_must_belong_to_user_and_exist(self):
        self.assertFalse(self.active(OTHER))
        self.assertFalse(self.active(session=OTHER))
        self.assertFalse(self.active(user=None))
        self.assertFalse(self.active(session=None))
        self.q('DELETE FROM auth.sessions')
        self.assertFalse(self.active())

    def test_session_deadline_enforced_even_before_cleanup(self):
        self.q("UPDATE auth.sessions SET not_after=now()-interval '1 second'")
        self.assertFalse(self.active())
        self.q("UPDATE auth.sessions SET not_after=now()+interval '1 hour'")
        self.assertTrue(self.active())

    def test_unconfirmed_anonymous_deleted_and_banned_users_denied(self):
        for assignment in ('email_confirmed_at=NULL', 'is_anonymous=true', 'is_anonymous=NULL', 'deleted_at=now()', "banned_until=now()+interval '1 hour'"):
            self.q('UPDATE auth.users SET ' + assignment)
            self.assertFalse(self.active())
            self.q('UPDATE auth.users SET email_confirmed_at=now(),is_anonymous=false,deleted_at=NULL,banned_until=NULL')
        self.q("UPDATE auth.users SET banned_until=now()-interval '1 hour'")
        self.assertTrue(self.active())
