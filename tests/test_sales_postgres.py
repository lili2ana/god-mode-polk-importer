"""Integration tests run only against the disposable localhost CI database."""
import os
import tempfile
import unittest
from pathlib import Path

import psycopg2
from psycopg2.extensions import parse_dsn

import load_sales_snapshot as loader
from sales_contract import COLUMNS
from test_sales_contract import fixture, sample

TEST_DSN=os.getenv('POLK_TEST_DB_URL')


@unittest.skipUnless(TEST_DSN, 'requires disposable localhost PostgreSQL')
class SalesPostgresTests(unittest.TestCase):
    def setUp(self):
        parts=parse_dsn(TEST_DSN)
        if parts.get('host') not in ('localhost','127.0.0.1') or parts.get('dbname')!='postgres' or parts.get('port')!='55432':
            raise RuntimeError('Integration tests require localhost:55432/postgres')
        self.conn=psycopg2.connect(TEST_DSN)
        self.addCleanup(self.conn.close)
        with self.conn, self.conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS public.polk_sales_stage_v2,public.polk_sales_v2,public.god_mode_feed_status CASCADE')
            cur.execute('CREATE TABLE public.polk_sales_stage_v2 ('+','.join(c+' text' for c in COLUMNS)+')')
            types={'ln_num':'integer','saledt':'date','price':'numeric'}
            cur.execute('CREATE TABLE public.polk_sales_v2 ('+','.join(c+' '+types.get(c,'text') for c in COLUMNS)+',updated_at timestamptz,PRIMARY KEY(parcel_id,ln_num))')
            cur.execute('''CREATE TABLE public.god_mode_feed_status (
                feed_name text PRIMARY KEY,source_file text,source_size_bytes bigint,source_row_count bigint,
                stage_row_count bigint,prod_row_count bigint,column_count integer,mapping_version text,status text,
                verified_at timestamptz,github_run_id text,commit_sha text,notes text,updated_at timestamptz)''')
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.rows=[sample(line=str(i)) for i in range(1,4)]
        self.manifest,self.local=fixture(self.root,self.rows)
        self.addCleanup(self.local.close)

    def stage(self,rows):
        with self.conn,self.conn.cursor() as cur:
            cur.executemany('INSERT INTO public.polk_sales_stage_v2 VALUES ('+','.join(['%s']*15)+')',rows)

    def counts(self):
        with self.conn,self.conn.cursor() as cur:
            cur.execute('SELECT (SELECT count(*) FROM public.polk_sales_stage_v2),(SELECT count(*) FROM public.polk_sales_v2),(SELECT count(*) FROM public.god_mode_feed_status)')
            return cur.fetchone()

    def test_partial_stage_resumes_and_publishes_atomically(self):
        self.stage(self.rows[:1])
        loader.load(self.conn,self.local,self.manifest,self.root)
        self.assertEqual(self.counts(),(0,3,1))
        self.assertTrue(self.manifest['production_verified'])

    def test_tampered_stage_never_changes_production(self):
        self.stage([sample(price='42')])
        with self.assertRaises(ValueError):
            loader.load(self.conn,self.local,self.manifest,self.root)
        self.assertEqual(self.counts(),(1,0,0))

    def test_marker_failure_rolls_back_production_and_keeps_stage(self):
        with self.conn,self.conn.cursor() as cur:
            cur.execute("ALTER TABLE public.god_mode_feed_status ADD CONSTRAINT fail_publication CHECK (status <> 'verified')")
        with self.assertRaises(psycopg2.IntegrityError):
            loader.load(self.conn,self.local,self.manifest,self.root)
        self.assertEqual(self.counts(),(3,0,0))
        self.assertFalse(self.manifest['production_verified'])
        with self.conn,self.conn.cursor() as cur:
            cur.execute('ALTER TABLE public.god_mode_feed_status DROP CONSTRAINT fail_publication')
        loader.load(self.conn,self.local,self.manifest,self.root)
        self.assertEqual(self.counts(),(0,3,1))

    def test_extra_production_row_rolls_back_merge(self):
        with self.conn,self.conn.cursor() as cur:
            cur.execute("INSERT INTO public.polk_sales_v2(parcel_id,ln_num,price) VALUES ('272717741014000999',1,77)")
        with self.assertRaises(ValueError):
            loader.load(self.conn,self.local,self.manifest,self.root)
        self.assertEqual(self.counts(),(3,1,0))

    def test_concurrent_loader_is_rejected(self):
        other=psycopg2.connect(TEST_DSN)
        try:
            with other.cursor() as cur:
                cur.execute('SELECT pg_advisory_lock(%s)',(loader.LOCK_ID,))
            other.commit()
            with self.assertRaises(RuntimeError):
                loader.load(self.conn,self.local,self.manifest,self.root)
            self.assertEqual(self.counts(),(0,0,0))
        finally:
            other.close()

    def test_interrupted_copy_preserves_committed_shards_and_resumes(self):
        source_dir=self.root/'interrupted'
        source_dir.mkdir()
        rows=[sample(parcel=f'272717741014{i:06d}') for i in range(10)]
        manifest,local=fixture(source_dir,rows)
        self.addCleanup(local.close)
        victim=local.execute('SELECT parcel FROM expected WHERE shard=1 LIMIT 1').fetchone()[0]
        first_shard=local.execute('SELECT count(*) FROM expected WHERE shard=0').fetchone()[0]
        self.assertGreater(first_shard,0)
        with self.conn,self.conn.cursor() as cur:
            cur.execute('ALTER TABLE public.polk_sales_stage_v2 ADD CONSTRAINT injected_copy_failure CHECK (parcel_id <> %s)',(victim,))
        with self.assertRaises(psycopg2.IntegrityError):
            loader.load(self.conn,local,manifest,source_dir)
        self.assertEqual(self.counts(),(first_shard,0,0))
        with self.conn,self.conn.cursor() as cur:
            cur.execute('ALTER TABLE public.polk_sales_stage_v2 DROP CONSTRAINT injected_copy_failure')
        loader.load(self.conn,local,manifest,source_dir)
        self.assertEqual(self.counts(),(0,10,1))


if __name__ == '__main__':
    unittest.main()
