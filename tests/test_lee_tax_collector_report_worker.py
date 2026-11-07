"""Fail-closed validation tests, no synthetic rows are written to staging."""
import unittest
from io import StringIO
from datetime import date
from lee_tax_collector_report_worker import (
    normalize_strap, interpret_tax_status, parse_report_csv,
    allowed_source_url, staging_dsn_is_safe, TaxReportError,
)

class LeeTaxReportTests(unittest.TestCase):
    def test_official_portal_only(self):
        self.assertTrue(allowed_source_url('https://county-taxes.net/fl-lee/reports'))
        self.assertFalse(allowed_source_url('https://leetc.com/api/parcel/delinquency'))
        self.assertFalse(allowed_source_url('https://county-taxes.net.evil.com/fl-lee/reports'))

    def test_strap_keeps_leading_zero(self):
        self.assertEqual(normalize_strap('01-44-26-L1-01003.0020'),'014426L1010030020')
        with self.assertRaises(TaxReportError):normalize_strap('1234')

    def test_2026_unpaid_not_delinquent(self):
        status,claim=interpret_tax_status('UNPAID',2026,date(2026,11,7))
        self.assertEqual(status,'UNPAID')
        self.assertFalse(claim)

    def test_2026_early_delinquent_flag_rejected(self):
        with self.assertRaises(TaxReportError):
            interpret_tax_status('DELINQUENT',2026,date(2026,11,7))

    def test_old_unpaid_not_proof_of_delinquency(self):
        self.assertFalse(interpret_tax_status('UNPAID',2025,date(2026,11,7))[1])

    def test_prior_year_explicit_delinquent_is_only_a_claim(self):
        self.assertTrue(interpret_tax_status('DELINQUENT',2025,date(2026,11,7))[1])

    def test_unrecognized_status_rejected(self):
        with self.assertRaises(TaxReportError):
            interpret_tax_status('CURRENT PER API',2025,date(2026,11,7))

    def test_parsed_csv_requires_identifying_data(self):
        text=StringIO('parcel_id,tax_year,tax_status,amount_due\n01-44-26-L1-01003.0020,2025,DELINQUENT,123.45\n')
        rows=parse_report_csv(text,date(2026,11,7))
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['delinquent_claimed'])
        self.assertEqual(rows[0]['amount_due'],'123.45')

    def test_csv_rejects_invalid_amount(self):
        with self.assertRaises(TaxReportError):
            parse_report_csv(StringIO('parcel_id,tax_year,tax_status,amount_due\n01-44-26-L1-01003.0020,2025,DELINQUENT,-10\n'),date(2026,11,7))

    def test_csv_requires_all_columns(self):
        with self.assertRaises(TaxReportError):
            parse_report_csv(StringIO('parcel_id,tax_year,amount_due\n01-44-26-L1-01003.0020,2025,100\n'),date(2026,11,7))

    def test_rejects_production_dsn(self):
        self.assertTrue(staging_dsn_is_safe('postgresql://postgres:secret@db.ltwqgkcblvtscgldxvjw.supabase.co:5432/postgres'))
        self.assertFalse(staging_dsn_is_safe('postgresql://postgres:secret@db.someproduction.supabase.co/postgres'))

if __name__=='__main__':
    unittest.main()
