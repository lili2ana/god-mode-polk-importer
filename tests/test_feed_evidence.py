import tempfile
import unittest
import zipfile
from pathlib import Path

from probe_feed_evidence import inspect


class FeedEvidenceTests(unittest.TestCase):
    def probe(self, data, extra_member=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'feed.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('feed.csv', data)
                if extra_member:
                    archive.writestr('other.csv', data)
            return inspect(path)

    def test_multiline_and_escaped_quotes_preserved(self):
        result = self.probe('PARCEL_ID,LNNUM,DSCR\n001,1,"room, with ""quotes""\nand newline"\n001,2,ok\n')
        self.assertEqual(result['rows'], 2)
        self.assertTrue(result['keys'][1]['unique_complete_key'])
        self.assertFalse(result['production_verified'])
        self.assertFalse(result['parcel_inventory_reconciled'])

    def test_normalization_collision_prevents_key_approval(self):
        result = self.probe('PARCEL_ID,LNNUM\n001,1\n 001 ,1\n')
        self.assertEqual(result['keys'][1]['duplicate_rows'], 1)
        self.assertFalse(result['schema_candidate_ready'])

    def test_blank_parcel_blocks_all_candidates(self):
        result = self.probe('PARCEL_ID,LNNUM\n,1\n')
        self.assertFalse(result['schema_candidate_ready'])

    def test_wrong_row_width_blocks_approval(self):
        result = self.probe('PARCEL_ID,LNNUM\n001,1,extra\n')
        self.assertEqual(result['bad_width_rows'], 1)
        self.assertFalse(result['schema_candidate_ready'])

    def test_unescaped_inch_quote_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'physical line 2'):
            self.probe('PARCEL_ID,DSCR\n001,"DIG 24" INSTALL "\n')

    def test_invalid_encoding_is_not_replaced(self):
        with self.assertRaises(UnicodeDecodeError):
            self.probe(b'PARCEL_ID,DSCR\n001,\xff\n')

    def test_ambiguous_archive_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'one data member'):
            self.probe('PARCEL_ID\n001\n', extra_member=True)

    def test_duplicate_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate column'):
            self.probe('PARCEL_ID,PARCEL_ID\n001,001\n')

    def test_empty_data_cannot_pass(self):
        result = self.probe('PARCEL_ID,LNNUM\n')
        self.assertFalse(result['schema_candidate_ready'])


if __name__ == '__main__':
    unittest.main()
