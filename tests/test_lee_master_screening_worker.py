import unittest
from unittest.mock import MagicMock
from run_lee_master_screening import (
    staging_dsn_is_safe, execute_screening, STAGING_AUDIT_SQL,
    SOURCE_AUDIT_SQL, RELEASE_ASSERT_SQL, REFRESH_SQL,
)

class MasterScreeningTests(unittest.TestCase):
    def test_direct_staging_host_allowed(self):
        self.assertTrue(staging_dsn_is_safe('postgresql://postgres:secret@db.ltwqgkcblvtscgldxvjw.supabase.co:5432/postgres'))

    def test_pooler_must_have_project_scoped_user(self):
        self.assertTrue(staging_dsn_is_safe('postgresql://postgres.ltwqgkcblvtscgldxvjw:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres'))
        self.assertFalse(staging_dsn_is_safe('postgresql://postgres:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres'))

    def test_other_projects_or_hosts_rejected(self):
        for dsn in ('', 'postgresql://postgres:secret@localhost/postgres',
                    'postgresql://postgres:secret@db.other.supabase.co/postgres',
                    'https://db.ltwqgkcblvtscgldxvjw.supabase.co'):
            self.assertFalse(staging_dsn_is_safe(dsn))

    def test_dry_run_only_reads_source_view(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [('HOLD_FEMA_EVIDENCE', 2710, 0)]
        touched, rows = execute_screening(conn, False)
        self.assertEqual(touched, 0)
        self.assertEqual(rows, [('HOLD_FEMA_EVIDENCE',2710,0)])
        self.assertEqual(cur.execute.call_args.args[0], SOURCE_AUDIT_SQL)
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_write_refresh_and_release_assertion(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(3376,), (0,)]
        cur.fetchall.return_value = [('HOLD_FEMA_EVIDENCE',2710,0),('HOLD_GEOMETRY_RECONCILIATION',666,0)]
        touched, _ = execute_screening(conn, True)
        self.assertEqual(touched,3376)
        self.assertEqual([c.args[0] for c in cur.execute.call_args_list], [REFRESH_SQL,STAGING_AUDIT_SQL,RELEASE_ASSERT_SQL])
        conn.commit.assert_called_once()

    def test_write_aborts_on_unverified_release(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(3376,), (1,)]
        with self.assertRaisesRegex(RuntimeError, 'unverified lead'):
            execute_screening(conn, True)
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

if __name__=='__main__':
    unittest.main()
