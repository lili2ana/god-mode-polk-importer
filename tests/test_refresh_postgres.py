"""Real PostgreSQL tests for registry correction and transaction-ordered refresh."""
import os
import unittest
from pathlib import Path
import psycopg2
from psycopg2.extensions import parse_dsn

DSN = os.getenv('POLK_TEST_DB_URL')
MIGRATION = Path(__file__).resolve().parents[1] / 'supabase/migrations/20260922043239_lean_target_refresh.sql'


@unittest.skipUnless(DSN, 'requires disposable localhost PostgreSQL')
class RefreshPostgresTests(unittest.TestCase):
    def setUp(self):
        p = parse_dsn(DSN)
        if p.get('host') not in ('localhost','127.0.0.1') or p.get('port')!='55432' or p.get('dbname')!='postgres' or p.get('hostaddr'):
            raise RuntimeError('Disposable database required')
        self.conn = psycopg2.connect(DSN); self.addCleanup(self.conn.close)
        with self.conn, self.conn.cursor() as c:
            c.execute('DROP SCHEMA IF EXISTS god_mode_ops CASCADE; DROP SCHEMA IF EXISTS cron CASCADE; DROP TABLE IF EXISTS public.properties CASCADE')
            c.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF; END $$")
            c.execute('CREATE SCHEMA god_mode_ops; CREATE SCHEMA cron; CREATE TABLE public.properties(id integer PRIMARY KEY,parcel_id text)')
            c.execute('CREATE TABLE god_mode_ops.target_parcels(property_id integer PRIMARY KEY,parcel_id text,parcel_key text UNIQUE,refreshed_at timestamptz)')
            c.execute('CREATE TABLE god_mode_ops.feed_retention_policy(feed_name text PRIMARY KEY,destructive_cleanup_enabled boolean)')
            c.execute('CREATE TABLE cron.job(jobid bigint PRIMARY KEY,jobname text,username text,command text,active boolean)')
            c.execute("INSERT INTO cron.job VALUES(2,'god_mode_polk_daily_sync','postgres','select public.full_daily_market_refresh();',true),(7,'god_mode_refresh_target_parcels','postgres','select god_mode_ops.refresh_target_parcels();',true)")
            c.execute('''CREATE FUNCTION cron.alter_job(job_id bigint,schedule text DEFAULT NULL,command text DEFAULT NULL,database text DEFAULT NULL,username text DEFAULT NULL,active boolean DEFAULT NULL) RETURNS void LANGUAGE sql AS $$ UPDATE cron.job j SET command=coalesce($3,j.command),active=coalesce($6,j.active) WHERE j.jobid=$1 $$''')
            c.execute("CREATE OR REPLACE FUNCTION public.full_daily_market_refresh() RETURNS jsonb LANGUAGE sql AS $$ SELECT '{\"fetched\":1}'::jsonb $$")
            c.execute(MIGRATION.read_text())
            c.execute("INSERT INTO public.properties VALUES(1,'123456-789012-345678')")
            c.execute('SELECT god_mode_ops.refresh_target_parcels()')

    def execute(self, query):
        with self.conn, self.conn.cursor() as c:
            c.execute(query)
            return c.fetchone() if c.description else None

    def test_blank_and_null_remove_only_derived_entry(self):
        for value in ("''", 'NULL'):
            self.execute(f'UPDATE public.properties SET parcel_id={value}')
            self.assertEqual(self.execute('SELECT god_mode_ops.refresh_target_parcels()'), (0,))
            self.assertEqual(self.execute('SELECT count(*) FROM public.properties'), (1,))
            self.execute("UPDATE public.properties SET parcel_id='123456789012345678'")
            self.execute('SELECT god_mode_ops.refresh_target_parcels()')

    def test_invalid_key_rolls_back(self):
        self.execute("UPDATE public.properties SET parcel_id='bad'")
        with self.assertRaises(psycopg2.Error): self.execute('SELECT god_mode_ops.refresh_target_parcels()')
        self.assertEqual(self.execute('SELECT count(*) FROM god_mode_ops.target_parcels'), (1,))

    def test_key_swap_and_noop_refresh(self):
        self.execute("INSERT INTO public.properties VALUES(2,'999999999999999999')")
        self.execute('SELECT god_mode_ops.refresh_target_parcels()')
        self.execute("UPDATE public.properties SET parcel_id=CASE WHEN id=1 THEN '999999999999999999' ELSE '123456789012345678' END")
        self.assertEqual(self.execute('SELECT god_mode_ops.refresh_target_parcels()'), (2,))
        before = self.execute('SELECT max(refreshed_at) FROM god_mode_ops.target_parcels')
        self.execute('SELECT god_mode_ops.refresh_target_parcels()')
        self.assertEqual(before, self.execute('SELECT max(refreshed_at) FROM god_mode_ops.target_parcels'))

    def test_upstream_success_precedes_target_refresh(self):
        self.execute('''CREATE OR REPLACE FUNCTION public.full_daily_market_refresh() RETURNS jsonb LANGUAGE plpgsql AS $$ BEGIN UPDATE public.properties SET parcel_id='999999999999999999'; RETURN '{"fetched":1}'::jsonb; END $$''')
        self.execute('SELECT god_mode_ops.refresh_market_and_targets()')
        self.assertEqual(self.execute('SELECT parcel_key FROM god_mode_ops.target_parcels'), ('999999999999999999',))

    def test_upstream_failure_rolls_back_without_target_refresh(self):
        self.execute('''CREATE OR REPLACE FUNCTION public.full_daily_market_refresh() RETURNS jsonb LANGUAGE plpgsql AS $$ BEGIN UPDATE public.properties SET parcel_id='999999999999999999'; RAISE EXCEPTION 'injected upstream failure'; END $$''')
        with self.assertRaises(psycopg2.Error): self.execute('SELECT god_mode_ops.refresh_market_and_targets()')
        self.assertEqual(self.execute('SELECT parcel_key FROM god_mode_ops.target_parcels'), ('123456789012345678',))
        self.assertEqual(self.execute('SELECT parcel_id FROM public.properties'), ('123456-789012-345678',))

    def test_empty_or_capped_upstream_refused(self):
        for n in (0, 40000):
            self.execute(f'''CREATE OR REPLACE FUNCTION public.full_daily_market_refresh() RETURNS jsonb LANGUAGE sql AS $$ SELECT '{{"fetched":{n}}}'::jsonb $$''')
            with self.assertRaises(psycopg2.Error): self.execute('SELECT god_mode_ops.refresh_market_and_targets()')

    def test_cron_chain_and_private_invoker_permissions(self):
        self.assertEqual(self.execute("SELECT command FROM cron.job WHERE jobid=2"), ('select god_mode_ops.refresh_market_and_targets();',))
        self.assertEqual(self.execute('SELECT active FROM cron.job WHERE jobid=7'), (False,))
        self.assertEqual(self.execute("SELECT has_function_privilege('anon','god_mode_ops.refresh_market_and_targets()','EXECUTE')"), (False,))
