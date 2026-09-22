"""Private local property-profile read path with source evidence; no scoring claims."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from build_target_tax_snapshot import digest, parcel_key
from property_source import HEADERS


SOURCE_REVIEW_CLASSES = {
    '9910': ('Inaccessible tracts', 'county_inaccessible_tract'),
    '9350': ('Mineral Rights (Not Phos.)', 'county_mineral_rights'),
    '0989': ('Split and/or Combine in Progress', 'county_split_combine'),
}


def source_review_flags(parcel_rows):
    """Observed county labels are review evidence, never legal conclusions."""
    flags = []
    for row in parcel_rows:
        code, description = row['DORUS_CODE'].strip(), row['DORDESC1'].strip()
        contract = SOURCE_REVIEW_CLASSES.get(code)
        description_match = next((item for item in SOURCE_REVIEW_CLASSES.values()
                                  if item[0].casefold() == description.casefold()), None)
        if contract or description_match:
            expected, reason = contract or description_match
            flags.append({'reason': reason, 'feed': 'parcel',
                          'source_code': code, 'source_description': description,
                          'legal_conclusion_verified': False})
            if contract is None or expected.casefold() != description.casefold():
                flags.append({'reason': 'county_classification_mismatch', 'feed': 'parcel',
                              'source_code': code, 'source_description': description,
                              'legal_conclusion_verified': False})
    return flags


class PropertyProfiles:
    def __init__(self, directory):
        root = Path(directory)
        self.manifest = json.loads((root / 'manifest.json').read_text())
        self.path = root / 'profiles.sqlite'
        if self.manifest['status'] != 'READY' or digest(self.path) != self.manifest['snapshot_sha256']:
            raise ValueError('Unverified or changed local snapshot')

    def get(self, parcel):
        key = parcel_key(parcel)
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT 1 FROM targets WHERE parcel_key=?', (key,)).fetchone() is None:
                raise KeyError('Parcel is outside the target universe')
            result = {'parcel_key': key, 'production_verified': False,
                      'eligible_for_automated_acquisition': False, 'missing_feeds': [], 'feeds': {}}
            for feed, header in HEADERS.items():
                records = db.execute('SELECT payload,raw_legal FROM records WHERE feed=? AND parcel_key=? ORDER BY line', (feed, key)).fetchall()
                if not records:
                    result['missing_feeds'].append(feed)
                result['feeds'][feed] = {'source_sha256': self.manifest['feeds'][feed]['source_sha256'],
                    'rows': [dict(zip(header, json.loads(r[0]))) for r in records]}
                if feed == 'legal':
                    result['feeds'][feed]['raw_source_records'] = [r[1] for r in records]
            result['source_review_flags'] = source_review_flags(result['feeds']['parcel']['rows'])
            result['feed_completeness_review_required'] = bool(result['missing_feeds'])
            result['review_required'] = bool(result['missing_feeds'] or result['source_review_flags'])
            return result
