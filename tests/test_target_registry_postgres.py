"""Registry gates against disposable localhost PostgreSQL, never production."""
import os
import unittest

import psycopg2
from psycopg2.extensions import parse_dsn, TRANSACTION_STATUS_IDLE
from export_target_registry import export

DSN = os.getenv('POLK_TEST_DB_URL')


@unittest.skipUnless(DSN, 'requires disposable localhost PostgreSQL')
class TargetRegistryPostgresTests(unittest.TestCase):
    def setUp(self):
        parts = parse_dsn(DSN)
        if parts.get('host') not in ('localhost', '127.0.0.1') or parts.get('port') != '55432' or parts.get('dbname') != 'postgres' or parts.get('hostaddr'):
            raise RuntimeError('Tests require disposable localhost:55432/postgres')
        self.conn = psycopg2.connect(DSN)
        self.addCleanup(self.conn.close)
        with self.conn, self.conn.cursor() as c:
            c.execute('DROP SCHEMA IF EXISTS god_mode_ops CASCADE; DROP TABLE IF EXISTS public.properties')
            c.execute('CREATE SCHEMA god_mode_ops; CREATE TABLE public.properties(id integer PRIMARY KEY, parcel_id text)')
            c.execute('CREATE TABLE god_mode_ops.target_parcels(property_id integer PRIMARY KEY, parcel_key text)')
            c.execute('CREATE TABLE god_mode_ops.feed_retention_policy(feed_name text PRIMARY KEY, destructive_cleanup_enabled boolean)')
            c.execute("INSERT INTO public.properties VALUES (1,'123456-789012-345678')")
            c.execute("INSERT INTO god_mode_ops.target_parcels VALUES (1,'123456789012345678')")
            c.executemany('INSERT INTO god_mode_ops.feed_retention_policy VALUES (%s,false)', [(s,) for s in ('parcel','owner','legal','sales','parcel_tax','permits')])

    def test_valid_export_rolls_back_and_connection_stays_readonly(self):
        self.assertEqual(export(self.conn)['parcel_keys'], ['123456789012345678'])
        self.assertEqual(self.conn.get_transaction_status(), TRANSACTION_STATUS_IDLE)
        with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction), self.conn.cursor() as c:
            c.execute('DELETE FROM public.properties')
        self.conn.rollback()

    def test_blank_source_parcel_cannot_leave_stale_target(self):
        with self.conn, self.conn.cursor() as c:
            c.execute("UPDATE public.properties SET parcel_id='' WHERE id=1")
        with self.assertRaisesRegex(ValueError, 'differs'):
            export(self.conn)
        self.assertEqual(self.conn.get_transaction_status(), TRANSACTION_STATUS_IDLE)

    def test_missing_target_blocks(self):
        with self.conn, self.conn.cursor() as c:
            c.execute("INSERT INTO public.properties VALUES (2,'999999999999999999')")
        with self.assertRaisesRegex(ValueError, 'differs'):
            export(self.conn)

    def test_cleanup_enabled_blocks(self):
        with self.conn, self.conn.cursor() as c:
            c.execute("UPDATE god_mode_ops.feed_retention_policy SET destructive_cleanup_enabled=true WHERE feed_name='legal'")
        with self.assertRaisesRegex(ValueError, 'cleanup'):
            export(self.conn)
