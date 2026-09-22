"""Private local property-profile read path with source evidence; no scoring claims."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from build_target_tax_snapshot import digest, parcel_key
from property_source import HEADERS


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
            result['review_required'] = bool(result['missing_feeds'])
            return result
