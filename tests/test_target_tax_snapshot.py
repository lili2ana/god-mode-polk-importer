import csv
import io
import json
import sqlite3
import tempfile
import unittest
import subprocess
import sys
import zipfile
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from build_target_tax_snapshot import HEADER, PROJECT, build, digest, target_keys


class TargetTaxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = '123456789012345678'
        self.snapshot = {'project_ref': PROJECT, 'registry_drift': 0,
                         'captured_at': datetime.now(timezone.utc).isoformat(), 'parcel_keys': [self.key]}

    def row(self, key=None, line='1'):
        return [key or self.key, line, '001', 'District', 'Description', '100', '0', '100', '1.25', '0', '1.25']

    def archive(self, rows, header=HEADER):
        text = io.StringIO(newline='')
        writer = csv.writer(text)
        writer.writerow(header)
        writer.writerows(rows)
        path = self.root / 'source.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('FTP_CAMA/ftp_parceltax.txt', text.getvalue())
        return path

    def run_build(self, rows, **kwargs):
        path = self.archive(rows)
        return build(path, self.snapshot, self.root / 'out', kwargs.get('sha', digest(path)), kwargs.get('count', len(rows)))

    def test_filters_without_changing_values_or_archive(self):
        rows = [self.row('123456-789012-345678'), self.row(line='2'), self.row('999999999999999999')]
        report = self.run_build(rows)
        self.assertEqual((report['selected_rows'], report['source_rows']), (2, 3))
        self.assertFalse(report['production_verified'])
        self.assertFalse(report['tax_delinquency_inferred'])
        with closing(sqlite3.connect(self.root / 'out/target_tax.sqlite')) as db:
            actual = [json.loads(r[0]) for r in db.execute('SELECT payload FROM target_tax ORDER BY line')]
        self.assertEqual(actual, rows[:2])
        self.assertEqual(digest(self.root / 'source.zip'), digest(self.root / 'out' / (report['source_sha256'] + '.zip')))

    def test_non_target_invalid_row_is_not_silently_filtered(self):
        bad = self.row('999999999999999999')
        bad[-1] = 'NaN'
        with self.assertRaises(ValueError):
            self.run_build([self.row(), bad])
        self.assertFalse((self.root / 'out/target_tax.sqlite').exists())
        self.assertEqual(json.loads((self.root / 'out/manifest.json').read_text())['status'], 'BLOCKED')

    def test_duplicate_normalized_key_blocks(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate normalized'):
            self.run_build([self.row(line='01'), self.row('123456-789012-345678')])

    def test_missing_target_blocks(self):
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.run_build([self.row('999999999999999999')])

    def test_observed_leading_decimal_format(self):
        row = self.row()
        row[8:] = ['.22', '.22', '.00']
        self.assertEqual(self.run_build([row])['status'], 'READY')

    def test_wrong_count_blocks(self):
        with self.assertRaisesRegex(ValueError, 'row count'):
            self.run_build([self.row()], count=2)

    def test_wrong_fingerprint_blocks_before_output(self):
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            self.run_build([self.row()], sha='0' * 64)
        self.assertFalse((self.root / 'out').exists())

    def test_header_drift_blocks(self):
        path = self.archive([self.row()], header=HEADER[:-1] + ['DELINQUENT'])
        with self.assertRaisesRegex(ValueError, 'header'):
            build(path, self.snapshot, self.root / 'out', digest(path), 1)

    def test_registry_drift_duplicate_and_staleness_block(self):
        variants = [{'registry_drift': 1}, {'parcel_keys': [self.key, self.key]},
                    {'project_ref': 'wrong'}, {'parcel_keys': []},
                    {'captured_at': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                target_keys({**self.snapshot, **changes})

    def test_existing_output_not_overwritten(self):
        self.run_build([self.row()])
        before = digest(self.root / 'out/target_tax.sqlite')
        with self.assertRaises(FileExistsError):
            self.run_build([self.row()])
        self.assertEqual(before, digest(self.root / 'out/target_tax.sqlite'))

    def test_legacy_cli_load_is_disabled_before_any_io(self):
        script = Path(__file__).resolve().parents[1] / 'load_sales_snapshot.py'
        run = subprocess.run([sys.executable, str(script), '--load', '--workdir', str(self.root / 'must-not-exist')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn('Countywide production loading is disabled', run.stderr)
        self.assertFalse((self.root / 'must-not-exist').exists())


if __name__ == '__main__':
    unittest.main()
