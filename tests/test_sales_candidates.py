import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_sales_candidate_snapshot import build, reference, screen
from build_target_tax_snapshot import PROJECT, digest
from property_source import HEADERS
from sales_contract import HEADER


P1, P2 = '123456789012345678', '223456789012345678'


def sale(parcel=P1, line='1', **fields):
    row = [parcel, '17', line, '09/11/2026', '145000', '100', '10', 'I',
           'W ', 'Warranty Deed', '01', 'Q-Per examination of deed', 'A', 'B', 'N']
    for field, value in fields.items():
        row[HEADER.index(field)] = value
    return row


def parcel(key=P1, code='0100', description='Single Family'):
    return {'PARCEL_ID': key, 'DORUS_CODE': code, 'DORDESC': 'RES', 'DORDESC1': description}


def archive(path, header, rows):
    text = io.StringIO(newline='')
    writer = csv.writer(text)
    writer.writerow(header)
    writer.writerows(rows)
    with zipfile.ZipFile(path, 'w') as out:
        out.writestr('feed.txt', text.getvalue())


class CandidateTests(unittest.TestCase):
    def test_candidate_is_always_unqualified_and_requires_review(self):
        result = screen(sale(), parcel(), None, None)
        self.assertEqual(result['screen_status'], 'candidate_for_comparability_review')
        self.assertTrue(result['review_required'])
        self.assertFalse(result['qualified_comp'])
        self.assertFalse(result['eligible_for_automated_acquisition'])
        self.assertIn('sale_date_property_attributes_unverified', result['remaining_review'])

    def test_q_prefix_and_code_description_drift_do_not_pass(self):
        for code, description in [('05', 'Q-Multiple parcels'), ('03', 'Q-Physical characteristics changed after sale'),
                                  ('01', 'Q-Multiple parcels'), ('99', 'Q-Per examination of deed')]:
            with self.subTest(code=code, description=description):
                result = screen(sale(INSTRTYP=code, INSTRTYP_DSCR=description), parcel(), None, None)
                self.assertEqual(result['screen_status'], 'held_for_review')

    def test_price_missing_nominal_foreclosure_and_unknown_sale_type(self):
        for field, value, reason in [('PRICE', '', 'price_missing'),
                                     ('PRICE', '100.00', 'nominal_price_screen_at_most_100'),
                                     ('PRICE', '0', 'nominal_price_screen_at_most_100'),
                                     ('FORECLOSURE', '', 'foreclosure_flag_or_unknown'),
                                     ('FORECLOSURE', 'C', 'foreclosure_flag_or_unknown'),
                                     ('SALETYPE', 'X', 'sale_type_unrecognized')]:
            with self.subTest(field=field, value=value):
                self.assertIn(reason, screen(sale(**{field: value}), parcel(), None, None)['screen_reasons'])

    def test_county_risk_and_missing_parcel_are_preserved(self):
        result = screen(sale(), parcel(code='9910', description='Inaccessible tracts'), None, None)
        self.assertIn('county_inaccessible_tract', result['screen_reasons'])
        self.assertFalse(result['source_review_flags'][0]['legal_conclusion_verified'])
        self.assertIn('current_parcel_missing', screen(sale(), None, None, None)['screen_reasons'])

    def test_numeric_reference_aliases_and_unrecognized_references(self):
        self.assertEqual(reference(' 00010 '), '10')
        for value in ['', '000', 'A10', '1/2', '-1', '\u0661']:
            self.assertEqual(reference(value), '')
        self.assertIn('recording_reference_missing_or_unrecognized',
                      screen(sale(BOOK='A'), parcel(), None, None)['screen_reasons'])

    def test_global_sale_id_reuse_is_not_inferred_to_be_a_package(self):
        result = screen(sale(), parcel(), None, (200000, P1, P2))
        self.assertIn('sale_id_not_globally_unique', result['identity_observations'])
        self.assertEqual(result['screen_status'], 'candidate_for_comparability_review')
        self.assertFalse(result['qualified_comp'])
        self.assertIn('transaction_identity_unverified', result['remaining_review'])

    def run_build(self, root, sales, parcels=None, **overrides):
        parcels = parcels if parcels is not None else [parcel(), parcel(P2)]
        sales_path, parcels_path = root / 'sales.zip', root / 'parcels.zip'
        archive(sales_path, HEADER, sales)
        archive(parcels_path, HEADERS['parcel'], [[p.get(h, '') for h in HEADERS['parcel']] for p in parcels])
        args = dict(sales_zip=sales_path, parcel_zip=parcels_path,
                    target_export={'project_ref': PROJECT, 'registry_drift': 0, 'parcel_keys': [P1],
                                   'captured_at': datetime.now(timezone.utc).isoformat()},
                    output=root / 'out', sales_sha256=digest(sales_path), parcel_sha256=digest(parcels_path),
                    sales_rows=len(sales), parcel_rows=len(parcels), start='2022-09-11', as_of='2026-09-11')
        args.update(overrides)
        return build(**args)

    def test_whole_county_retention_boundaries_and_full_history_grouping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sales = [sale(SALEDT='09/11/2022'),
                     sale(P2, SALE_ID='18', BOOK='200'),
                     sale(P2, line='2', SALEDT='01/01/2000', BOOK='00100', SALE_ID='00017'),
                     sale(line='2', SALEDT='', SALE_ID='19', BOOK='300'),
                     sale(line='3', SALEDT='09/12/2026', SALE_ID='20', BOOK='400')]
            result = self.run_build(root, sales)
            self.assertEqual(result['counts']['window_rows'], 2)
            self.assertEqual(result['counts']['non_target_candidate_rows'], 1)
            self.assertEqual(result['counts']['undated_rows'], 1)
            self.assertEqual(result['counts']['after_as_of_rows'], 1)
            self.assertEqual(result['counts']['before_window_rows'], 1)
            self.assertEqual(result['full_history_multi_parcel_groups'], {'recording': 1, 'sale_id': 1})
            with closing(sqlite3.connect(root / 'out/sales_candidates.sqlite')) as db:
                assessment = json.loads(db.execute('SELECT assessment FROM sales WHERE parcel=?', (P1,)).fetchone()[0])
                self.assertIn('recording_spans_multiple_parcels', assessment['screen_reasons'])
                self.assertIn('sale_id_not_globally_unique', assessment['identity_observations'])
            self.assertFalse(result['qualified_comps'])
            self.assertFalse(result['read_cutover_allowed'])
            self.assertEqual(digest(root / 'out/sales.zip'), result['sales_sha256'])

    def test_duplicate_canonical_key_fails_even_outside_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(sqlite3.IntegrityError):
                self.run_build(root, [sale(SALEDT='01/01/2000'), sale(line='01', SALEDT='01/01/2000')])
            self.assertEqual(json.loads((root / 'out/manifest.json').read_text())['status'], 'BLOCKED')
            self.assertFalse((root / 'out/sales_candidates.sqlite').exists())

    def test_malformed_excluded_record_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                self.run_build(Path(temp), [sale(SALEDT='01/01/2000', PRICE='NaN')])

    def test_hash_count_and_stale_targets_fail_closed(self):
        overrides = [dict(sales_sha256='0' * 64), dict(sales_rows=2),
                     dict(target_export={'project_ref': PROJECT, 'registry_drift': 0, 'parcel_keys': [P1],
                          'captured_at': (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()})]
        for override in overrides:
            with self.subTest(override=list(override)), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):
                    self.run_build(Path(temp), [sale()], **override)

    def test_duplicate_parcel_dimension_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Duplicate current parcel'):
                self.run_build(Path(temp), [sale()], parcels=[parcel(), parcel()])

    def test_no_overwrite_and_no_sales_for_target_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = self.run_build(root, [sale(P2)])
            self.assertEqual(report['counts']['non_target_candidate_rows'], 1)
            with self.assertRaises(FileExistsError):
                self.run_build(root, [sale()])


if __name__ == '__main__':
    unittest.main()
