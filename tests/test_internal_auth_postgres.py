"""Credential isolation and scheduled call routing on disposable PostgreSQL only."""
import os
import unittest
from pathlib import Path
import psycopg2
from psycopg2.extensions import parse_dsn

DSN = os.getenv('POLK_TEST_DB_URL')
MIGRATION = Path(__file__).resolve().parents[1] / 'supabase/migrations/20260922053049_secure_internal_workers.sql'

@unittest.skipUnless(DSN, 'requires disposable localhost PostgreSQL')
class InternalAuthPostgresTests(unittest.TestCase):
    def setUp(self):
        p = parse_dsn(DSN)
        if p.get('host') not in ('localhost','127.0.0.1') or p.get('port') != '55432' or p.get('dbname') != 'postgres' or p.get('hostaddr'):
            raise RuntimeError('Disposable database required')
        self.db = psycopg2.connect(DSN); self.addCleanup(self.db.close)
        with self.db, self.db.cursor() as c:
            c.execute('DROP FUNCTION IF EXISTS public.god_mode_check_internal_token(text,text); DROP SCHEMA IF EXISTS god_mode_ops CASCADE; DROP SCHEMA IF EXISTS vault CASCADE; DROP SCHEMA IF EXISTS net CASCADE; DROP SCHEMA IF EXISTS cron CASCADE')
            c.execute('CREATE SCHEMA god_mode_ops; CREATE SCHEMA vault; CREATE SCHEMA net; CREATE SCHEMA cron; CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions')
            c.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF; END $$")
            # Vault is a disposable fixture; production Vault provides encryption.
            c.execute('CREATE TABLE vault.secrets(id uuid DEFAULT gen_random_uuid(),name text UNIQUE,decrypted_secret text); CREATE VIEW vault.decrypted_secrets AS SELECT * FROM vault.secrets')
            c.execute('''CREATE FUNCTION vault.create_secret(new_secret text,new_name text,new_description text) RETURNS uuid LANGUAGE sql AS $$ INSERT INTO vault.secrets(name,decrypted_secret) VALUES($2,$1) RETURNING id $$''')
            c.execute('DROP TYPE IF EXISTS extensions.http_request CASCADE; DROP TYPE IF EXISTS extensions.http_response CASCADE; DROP TYPE IF EXISTS extensions.http_header CASCADE; DROP TYPE IF EXISTS extensions.http_method CASCADE')
            c.execute("CREATE TYPE extensions.http_method AS ENUM ('GET','POST')")
            c.execute('CREATE TYPE extensions.http_header AS (field varchar,value varchar); CREATE TYPE extensions.http_request AS (method extensions.http_method,uri varchar,headers extensions.http_header[],content_type varchar,content varchar); CREATE TYPE extensions.http_response AS (status integer,content_type varchar,headers extensions.http_header[],content varchar)')
            c.execute("CREATE FUNCTION extensions.http_header(varchar,varchar) RETURNS extensions.http_header LANGUAGE sql AS $$ SELECT ($1,$2)::extensions.http_header $$")
            c.execute("CREATE OR REPLACE FUNCTION vault.update_secret(secret_id uuid,new_secret text) RETURNS void LANGUAGE sql AS $$ UPDATE vault.secrets SET decrypted_secret=$2 WHERE id=$1 $$")
            c.execute('CREATE TABLE net.requests(id bigserial PRIMARY KEY,url text,method text,headers jsonb)')
            c.execute('CREATE TABLE net.http_request_queue(id bigint); CREATE TABLE net._http_response(id bigint); GRANT ALL ON net.http_request_queue,net._http_response TO PUBLIC,anon,authenticated')
            c.execute('''CREATE FUNCTION net.http_post(url text,body jsonb DEFAULT '{}'::jsonb,params jsonb DEFAULT '{}'::jsonb,headers jsonb DEFAULT '{}'::jsonb,timeout_milliseconds integer DEFAULT 1000) RETURNS bigint LANGUAGE sql AS $$ INSERT INTO net.requests(url,method,headers) VALUES($1,'POST',$4) RETURNING id $$''')
            c.execute('''CREATE FUNCTION net.http_get(url text,params jsonb DEFAULT '{}'::jsonb,headers jsonb DEFAULT '{}'::jsonb,timeout_milliseconds integer DEFAULT 1000) RETURNS bigint LANGUAGE sql AS $$ INSERT INTO net.requests(url,method,headers) VALUES($1,'GET',$3) RETURNING id $$''')
            c.execute('CREATE TABLE cron.job(jobid bigint PRIMARY KEY,jobname text,username text,command text,active boolean,schedule text)')
            c.execute("INSERT INTO cron.job VALUES(4,'god_mode_dd_worker_daily','postgres','old',true,'5 6 * * *'),(5,'god_mode_dd_finalize_daily','postgres','old',true,'15 6 * * *')")
            c.execute('''CREATE FUNCTION cron.alter_job(job_id bigint,schedule text DEFAULT NULL,command text DEFAULT NULL,database text DEFAULT NULL,username text DEFAULT NULL,active boolean DEFAULT NULL) RETURNS void LANGUAGE sql AS $$ UPDATE cron.job j SET command=coalesce($3,j.command),active=coalesce($6,j.active) WHERE j.jobid=$1 $$''')
            c.execute("""CREATE FUNCTION extensions.http(request extensions.http_request) RETURNS extensions.http_response LANGUAGE plpgsql AS $$ BEGIN INSERT INTO net.requests(url,method,headers) VALUES(request.uri,request.method::text,jsonb_build_object('x-god-mode-token',(request.headers[1]).value)); RETURN (200,'application/json',NULL,'{"ok":true,"errors":0,"processed":1}')::extensions.http_response; END $$""")
            c.execute("CREATE OR REPLACE FUNCTION extensions.http_set_curlopt(varchar,varchar) RETURNS boolean LANGUAGE sql AS $$ SELECT true $$; CREATE OR REPLACE FUNCTION extensions.http_list_curlopt() RETURNS TABLE(curlopt text,value text) LANGUAGE sql AS $$ SELECT 'CURLOPT_TIMEOUT_MS'::text,'5000'::text $$")
            c.execute(MIGRATION.read_text())
            c.execute(MIGRATION.with_name('20260922054152_private_internal_transport.sql').read_text())
            c.execute(MIGRATION.with_name('20260922054310_synchronous_private_workers.sql').read_text())
            c.execute(MIGRATION.with_name('20260922060527_secure_title_access_worker.sql').read_text(encoding='utf-8'))
            c.execute(MIGRATION.with_name('20260922080459_secure_dashboard_counts.sql').read_text(encoding='utf-8'))

    def q(self,sql,args=None):
        with self.db,self.db.cursor() as c:
            c.execute(sql,args); return c.fetchone() if c.description else None

    def test_fresh_unique_credentials_with_no_table_grants(self):
        self.assertEqual(self.q('SELECT count(*),count(DISTINCT token_digest) FROM god_mode_ops.internal_function_credentials'),(5,5))
        self.assertEqual(self.q("SELECT has_table_privilege('anon','god_mode_ops.internal_function_credentials','SELECT'),has_table_privilege('service_role','god_mode_ops.internal_function_credentials','SELECT')"),(False,False))
        self.assertEqual(self.q("SELECT has_function_privilege('anon','public.god_mode_check_internal_token(text,text)','EXECUTE'),has_function_privilege('authenticated','public.god_mode_check_internal_token(text,text)','EXECUTE'),has_function_privilege('service_role','public.god_mode_check_internal_token(text,text)','EXECUTE')"),(False,False,True))

    def test_exact_scope_and_disabled_credentials(self):
        digest=self.q("SELECT token_digest FROM god_mode_ops.internal_function_credentials WHERE scope='god-mode-dd-worker'")[0]
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-dd-worker',%s)",(digest,)),(True,))
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-dd-finalize',%s)",(digest,)),(False,))
        self.q("UPDATE god_mode_ops.internal_function_credentials SET enabled=false WHERE scope='god-mode-dd-worker'")
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-dd-worker',%s)",(digest,)),(False,))

    def test_transport_does_not_use_shared_queue(self):
        body=self.q("SELECT prosrc FROM pg_proc WHERE oid='god_mode_ops.invoke_internal_worker(text,boolean,integer)'::regprocedure")[0]
        self.assertNotIn("net.http",body)
        self.assertIn("extensions.http",body)
        for role in ('anon','authenticated'):
            for table in ('net.http_request_queue','net._http_response'):
                for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                    self.assertEqual(self.q('SELECT has_table_privilege(%s,%s,%s)',(role,table,privilege)),(False,))

    def test_private_invoker_and_fixed_destination(self):
        self.assertEqual(self.q("SELECT has_function_privilege('anon','god_mode_ops.invoke_internal_worker(text,boolean,integer)','EXECUTE'),has_function_privilege('service_role','god_mode_ops.invoke_internal_worker(text,boolean,integer)','EXECUTE')"),(False,False))
        for args in [('https://evil.invalid',True,1),('god-mode-dd-worker',False,6),('god-mode-crm-feed',False,1),(None,True,1)]:
            with self.assertRaises(psycopg2.Error):self.q('SELECT god_mode_ops.invoke_internal_worker(%s,%s,%s)',args)
        self.assertEqual(self.q('SELECT count(*) FROM net.requests'),(0,))

    def test_worker_post_and_crm_auth_only_get_with_vault_header(self):
        self.q("SELECT god_mode_ops.invoke_internal_worker('god-mode-dd-worker',false,1)")
        self.assertEqual(self.q("SELECT method,url LIKE '%god-mode-dd-worker?limit=1',(headers->>'x-god-mode-token') ~ '^[a-f0-9]{64}$' FROM net.requests"),('POST',True,True))
        self.q("SELECT god_mode_ops.invoke_internal_worker('god-mode-crm-feed',true)")
        self.assertEqual(self.q("SELECT method,url LIKE '%god-mode-crm-feed?check=auth' FROM net.requests ORDER BY id DESC LIMIT 1"),('GET',True))
        self.assertEqual(self.q('SELECT count(*) FROM god_mode_ops.internal_function_requests'),(2,))

    def test_schedule_preserved_and_missing_secret_fails_without_dispatch(self):
        self.assertEqual(self.q("SELECT schedule,active,command LIKE '%invoke_internal_worker%' FROM cron.job WHERE jobid=4"),('5 6 * * *',True,True))
        self.q("DELETE FROM vault.secrets WHERE name='internal_god_mode_dd_worker'")
        with self.assertRaises(psycopg2.Error): self.q("SELECT god_mode_ops.invoke_internal_worker('god-mode-dd-worker')")
        self.assertEqual(self.q('SELECT count(*) FROM net.requests'),(0,))

    def test_title_access_has_independent_token_and_manual_bounded_route(self):
        digest=self.q("SELECT token_digest FROM god_mode_ops.internal_function_credentials WHERE scope='god-mode-title-access-worker'")[0]
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-title-access-worker',%s)",(digest,)),(True,))
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-dd-worker',%s)",(digest,)),(False,))
        self.q("SELECT god_mode_ops.invoke_internal_worker('god-mode-title-access-worker',false,1)")
        self.assertEqual(self.q("SELECT method,url LIKE '%god-mode-title-access-worker?limit=1' FROM net.requests"),('POST',True))
        self.assertEqual(self.q("SELECT count(*) FROM cron.job WHERE command LIKE '%title-access%'"),(0,))

    def test_dashboard_scope_is_independent_and_dispatcher_health_only(self):
        digest=self.q("SELECT token_digest FROM god_mode_ops.internal_function_credentials WHERE scope='god-mode-dashboard'")[0]
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-dashboard',%s)",(digest,)),(True,))
        self.assertEqual(self.q("SELECT public.god_mode_check_internal_token('god-mode-crm-feed',%s)",(digest,)),(False,))
        with self.assertRaises(psycopg2.Error): self.q("SELECT god_mode_ops.invoke_internal_worker('god-mode-dashboard',false,1)")
        self.assertEqual(self.q('SELECT count(*) FROM net.requests'),(0,))
        self.q("SELECT god_mode_ops.invoke_internal_worker('god-mode-dashboard',true,1)")
        self.assertEqual(self.q("SELECT method,url LIKE '%god-mode-dashboard?check=auth' FROM net.requests"),('GET',True))
