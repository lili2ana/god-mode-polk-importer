import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from build_property_snapshot import build, row_digest
from build_target_tax_snapshot import PROJECT, digest
from property_profile import PropertyProfiles
from property_source import HEADERS, legal_rows

KEY = '123456789012345678'


class PropertyProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = {'project_ref': PROJECT, 'captured_at': datetime.now(timezone.utc).isoformat(),
                       'registry_drift': 0, 'parcel_keys': [KEY]}
        self.data = {'parcel': [[KEY] + [''] * 35],
                     'owner': [[KEY, '1', 'Owner A', '50.00'] + [''] * 7,
                               [KEY, '2', 'Owner B', '50.00'] + [''] * 7],
                     'legal': [[KEY, '1', '12', '34', '56', '000001', '000001', 'LOT A']]}

    def archives(self):
        contracts = {}
        for feed, data in self.data.items():
            text = io.StringIO(newline='')
            writer = csv.writer(text, quoting=csv.QUOTE_ALL)
            writer.writerow(HEADERS[feed]); writer.writerows(data)
            with zipfile.ZipFile(self.root / f'{feed}.zip', 'w') as z:
                z.writestr(feed + '.txt', text.getvalue())
            contracts[feed] = {'rows': len(data), 'sha256': digest(self.root / f'{feed}.zip')}
        return contracts

    def test_profile_preserves_multiple_owners_legal_and_provenance(self):
        build(self.root, self.archives(), self.target, self.root / 'out')
        profiles = PropertyProfiles(self.root / 'out')
        result = profiles.get('123456-789012-345678')
        self.assertEqual(len(result['feeds']['owner']['rows']), 2)
        self.assertEqual(result['feeds']['owner']['rows'][1]['NAME'], 'Owner B')
        self.assertIn('LOT A', result['feeds']['legal']['raw_source_records'][0])
        self.assertFalse(result['production_verified'])
        with self.assertRaises(KeyError): profiles.get('999999999999999999')

    def test_source_owner_gap_is_explicit_and_never_eligible(self):
        self.data['owner'] = [[ '999999999999999999', '1', 'X', '100'] + [''] * 7]
        report = build(self.root, self.archives(), self.target, self.root / 'out')
        self.assertEqual(report['profiles_requiring_review'], 1)
        self.assertFalse(report['read_cutover_allowed'])
        profile = PropertyProfiles(self.root / 'out').get(KEY)
        self.assertEqual(profile['missing_feeds'], ['owner'])
        self.assertTrue(profile['review_required'])
        self.assertFalse(profile['eligible_for_automated_acquisition'])

    def test_missing_parcel_blocks(self):
        self.data['parcel'][0][0] = '999999999999999999'
        with self.assertRaisesRegex(ValueError, 'coverage'):
            build(self.root, self.archives(), self.target, self.root / 'out')
        self.assertFalse((self.root / 'out/profiles.sqlite').exists())

    def test_duplicate_normalized_line_blocks(self):
        self.data['owner'][1][1] = '01'
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            build(self.root, self.archives(), self.target, self.root / 'out')

    def test_changed_snapshot_rejected(self):
        build(self.root, self.archives(), self.target, self.root / 'out')
        with closing(sqlite3.connect(self.root / 'out/profiles.sqlite')) as db:
            db.execute("DELETE FROM records WHERE feed='owner'"); db.commit()
        with self.assertRaisesRegex(ValueError, 'changed'):
            PropertyProfiles(self.root / 'out')

    def test_legal_unescaped_quote_is_preserved(self):
        line = f'"{KEY}","1","12","34","56","000001","000001","RUN 12\" E, THEN NORTH  "\r\n'
        header = ','.join('"'+h+'"' for h in HEADERS['legal']) + '\r\n'
        row, raw = next(legal_rows(io.StringIO(header + line)))
        self.assertEqual(row[-1], 'RUN 12" E, THEN NORTH')
        self.assertEqual(raw, line)

    def test_ambiguous_legal_line_is_not_appended(self):
        header = ','.join(HEADERS['legal'])+'\n'
        line = f'"{KEY}","1","12","34","56","1","1","LOT A"\n'
        with self.assertRaisesRegex(ValueError, 'boundary'):
            list(legal_rows(io.StringIO(header + line + 'unknown continuation\n')))

    def test_legal_unbalanced_wrapper_blocks(self):
        header = ','.join(HEADERS['legal'])+'\n'
        with self.assertRaisesRegex(ValueError, 'wrapper'):
            list(legal_rows(io.StringIO(header + f'"{KEY}","1","12","34","56","1","1","OPEN\n')))

    def test_owner_comparison_normalizes_numeric_representation(self):
        row = self.data['owner'][0]
        self.assertEqual(row_digest('owner', row), row_digest('owner', [row[0], '01', row[2], '50']+row[4:]))
