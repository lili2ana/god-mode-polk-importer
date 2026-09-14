import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

from sales_contract import HEADER, PROJECT, normalize, prepare, reconcile, require_writable, typed, validate_target


def sample(line='1', price='100000.50', date='02/29/2024', parcel='272717741014000180'):
    return [parcel, 'sale-id', line, date, price, '001', '02', '', '', '', '', '', 'Owner A', 'Owner B', '']


def fixture(root, rows, partitions=2):
    path = root/'source.zip'
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(HEADER)
    writer.writerows(rows)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('sales.txt', stream.getvalue())
    manifest = prepare(path, root/'source.sqlite', len(rows), partitions)
    return manifest, sqlite3.connect(root/'source.sqlite')


class SalesContractTests(unittest.TestCase):
    def test_invalid_calendar_date(self):
        with self.assertRaises(ValueError):
            normalize(sample(date='02/29/2023'))

    def test_finite_prices(self):
        for value in ('NaN', 'Infinity', '-1', '1e6', '$100'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(sample(price=value))
        self.assertEqual(str(typed(sample(price='.50'))[4]), '0.50')

    def test_integer_and_parcel_boundaries(self):
        for value in ('2147483648', '-1', '1.0', 'abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(sample(line=value))
        with self.assertRaises(ValueError):
            normalize(sample(parcel='not-a-parcel'))

    def test_null_numeric_values_remain_unknown(self):
        row = typed(sample(price='', date=''))
        self.assertIsNone(row[3])
        self.assertIsNone(row[4])

    def test_canonical_collision_rejected_before_database_access(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            fixture(Path(tmp), [sample(line='01'), sample(line='1')])

    def test_same_count_wrong_values_and_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = fixture(Path(tmp), [sample(), sample(line='2')])
            try:
                self.assertEqual(reconcile(db,[sample()]),1)
                for rows in ([sample(price='42')], [sample(line='9')], [sample(),sample()]):
                    with self.assertRaises(ValueError):
                        reconcile(db, rows)
                self.assertEqual(reconcile(db, [typed(sample()),typed(sample(line='2'))],production=True),2)
            finally:
                db.close()

    def test_source_row_count_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, db = fixture(root,[sample()])
            db.close()
            with self.assertRaisesRegex(ValueError,'row count'):
                prepare(root/'source.zip',root/'other.sqlite',2)

    def test_nul_bytes_are_rejected(self):
        row=sample(); row[12]='bad\x00value'
        with self.assertRaises(ValueError):
            normalize(row)

    def test_preflight_stops_readonly_recovery_wrong_database(self):
        for state in [('on',False,'postgres'),('off',True,'postgres'),('off',False,'other')]:
            cur=Mock(); cur.fetchone.return_value=state
            with self.assertRaises(RuntimeError):
                require_writable(cur)
        cur=Mock(); cur.fetchone.return_value=('off',False,'postgres')
        require_writable(cur)

    def test_project_validation_rejects_substring_spoof_and_transaction_pooler(self):
        direct=f'postgresql://postgres:test@db.{PROJECT}.supabase.co:5432/postgres'
        self.assertEqual(validate_target(direct)['dbname'],'postgres')
        pool=f'postgresql://postgres.{PROJECT}:test@aws-0-us-east-1.pooler.supabase.com:5432/postgres'
        self.assertIn('pooler',validate_target(pool)['host'])
        for dsn in [direct.replace('.supabase.co','.supabase.co.evil.example'),
                    pool.replace(':5432',':6543'),direct+'?sslmode=disable',
                    direct+'?hostaddr=127.0.0.1',direct.replace('postgres:test','other:test')]:
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                validate_target(dsn)


if __name__ == '__main__':
    unittest.main()
