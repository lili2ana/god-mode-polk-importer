import os
import unittest
from pathlib import Path
import psycopg2
from psycopg2.extensions import parse_dsn
DSN=os.getenv('POLK_TEST_DB_URL')
SQL=(Path(__file__).resolve().parents[1]/'supabase/migrations/20260922054616_quarantine_unverified_dd.sql').read_text()
@unittest.skipUnless(DSN,'requires disposable localhost PostgreSQL')
class DDQuarantineTests(unittest.TestCase):
    def setUp(self):
        p=parse_dsn(DSN)
        if p.get('host') not in ('localhost','127.0.0.1') or p.get('port')!='55432' or p.get('dbname')!='postgres' or p.get('hostaddr'):raise RuntimeError('Disposable database required')
        self.db=psycopg2.connect(DSN);self.addCleanup(self.db.close)
        with self.db,self.db.cursor() as c:
            c.execute('DROP SCHEMA IF EXISTS god_mode_ops CASCADE; CREATE SCHEMA god_mode_ops; DROP TABLE IF EXISTS public.due_diligence_reviews CASCADE')
            c.execute("DO $$ DECLARE r text; BEGIN FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN EXECUTE format('CREATE ROLE %I',r); END IF; END LOOP; END $$")
            c.execute('CREATE TABLE public.due_diligence_reviews(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),status text,dd_score numeric,comps_status text,underwriting_status text,findings jsonb)')
            c.execute("INSERT INTO public.due_diligence_reviews(status,dd_score,findings) VALUES('review_required',90,'{\"underwriting\":{\"preliminary_mao\":82000}}'),('approved',91,'{\"underwriting\":{\"preliminary_mao\":82001}}'),('review_required',0,'{\"underwriting\":{\"eligible_for_automated_acquisition\":false}}')")
    def test_backup_and_only_legacy_machine_review_quarantined(self):
        with self.db,self.db.cursor() as c:
            c.execute(SQL)
            c.execute('SELECT count(*),max((original_row->>\'dd_score\')::numeric) FROM god_mode_ops.dd_security_backup');self.assertEqual(c.fetchone(),(1,90))
            c.execute("SELECT dd_score,findings->'underwriting'->>'preliminary_mao' FROM public.due_diligence_reviews WHERE id IN(SELECT review_id FROM god_mode_ops.dd_security_backup)");self.assertEqual(c.fetchone(),(0,None))
            c.execute("SELECT dd_score FROM public.due_diligence_reviews WHERE status='approved'");self.assertEqual(c.fetchone(),(91,))
            c.execute("SELECT has_table_privilege('anon','god_mode_ops.dd_security_backup','SELECT')");self.assertEqual(c.fetchone(),(False,))
    def test_unexpected_population_aborts_without_changing_reviews(self):
        with self.db,self.db.cursor() as c:c.execute("INSERT INTO public.due_diligence_reviews(status,dd_score,findings) SELECT 'review_required',90,'{}'::jsonb FROM generate_series(1,101)")
        with self.assertRaises(psycopg2.Error):
            with self.db,self.db.cursor() as c:c.execute(SQL)
        with self.db,self.db.cursor() as c:
            c.execute('SELECT count(*) FROM public.due_diligence_reviews WHERE dd_score=90');self.assertEqual(c.fetchone(),(102,))
