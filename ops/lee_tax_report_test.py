import datetime
import unittest
from lee_tax_report import candidates


class TaxReportTests(unittest.TestCase):
    def snapshot(self, year='2024', status='Issued'):
        row = [year, '01-44-26-L1-01003.0020', '-- None --', status, '2513857',
               'TEST ADDRESS LEHIGH ACRES', 'TEST OWNER'] + [''] * 9 + ['Unpaid', '00', '351.29', '432.15']
        return {'source_url': 'https://county-taxes.net/fl-lee/reports',
                'checked_at': '2026-10-14T08:00:00+00:00', 'complete': True,
                'reported_total': 1, 'rows': [row]}

    def test_candidate_cannot_approve(self):
        d = candidates(self.snapshot(), datetime.date(2026, 10, 14))['candidates'][0]
        self.assertFalse(d['qualifying_distress']); self.assertFalse(d['release_search_complete'])
        self.assertEqual(d['delinquent_since'], '2025-04-01')

    def test_redeemed_is_excluded_even_with_unpaid_balance(self):
        r = candidates(self.snapshot(status='Redeemed'))
        self.assertEqual(r['candidates'], [])
        self.assertEqual(r['excluded'], {'CERTIFICATE_NOT_ISSUED': 1})

    def test_recent_tax_year_does_not_meet_age_threshold(self):
        r = candidates(self.snapshot(year='2025'), datetime.date(2026, 10, 14))
        self.assertEqual(r['candidates'], [])

    def test_incomplete_and_duplicate_reports_fail(self):
        d = self.snapshot(); d['reported_total'] = 2
        with self.assertRaises(ValueError): candidates(d)
        d['rows'] *= 2
        self.assertEqual(candidates(d)['excluded'], {'DUPLICATE_CERTIFICATE_REVIEW': 2})

    def test_wrong_county_fails(self):
        d = self.snapshot(); d['source_url'] = 'https://county-taxes.net/fl-other/reports'
        with self.assertRaises(ValueError): candidates(d)

    def test_exact_twelve_months_fails(self):
        r = candidates(self.snapshot(), datetime.date(2026, 4, 1))
        self.assertEqual(r['candidates'], [])


if __name__ == '__main__': unittest.main()
