"""Build parcel/owner/legal property profiles outside operational PostgreSQL."""
import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from build_target_tax_snapshot import digest, target_keys
from property_source import HEADERS, identity, rows


def comparison_fields(feed, row):
    values = list(row)
    if feed != 'parcel':
        values[1] = str(int(values[1]))
    if feed == 'owner' and values[3]:
        value = Decimal(values[3])
        values[3] = format(value.normalize(), 'f') if value else '0'
    return values


def row_digest(feed, row):
    # Matches PostgreSQL md5(jsonb_build_array(coalesce(column::text,''),...)::text).
    return hashlib.md5(json.dumps(comparison_fields(feed, row), ensure_ascii=False).encode()).hexdigest()


def build(source_dir, sources, target_snapshot, output):
    keys = target_keys(target_snapshot)
    source_dir, output = Path(source_dir), Path(output)
    if set(sources) != set(HEADERS):
        raise ValueError('Require all three source contracts')
    for feed, contract in sources.items():
        if contract['rows'] < 1 or digest(source_dir / f'{feed}.zip') != contract['sha256']:
            raise ValueError('Accepted source count/hash invalid')
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'BLOCKED', 'production_verified': False, 'read_cutover_allowed': False,
              'target_count': len(keys), 'target_captured_at': target_snapshot['captured_at'],
              'target_sha256': hashlib.sha256('\n'.join(sorted(keys)).encode()).hexdigest(),
              'feeds': {}, 'legal_parser': 'seven_quoted_numeric_fields_v1_raw_preserved'}
    try:
        with closing(sqlite3.connect(output / 'profiles.partial.sqlite')) as db, tempfile.TemporaryDirectory() as tmp:
            db.execute('CREATE TABLE records(feed TEXT,parcel_key TEXT,line INTEGER,payload TEXT NOT NULL,raw_legal TEXT,field_md5 TEXT NOT NULL,PRIMARY KEY(feed,parcel_key,line)) WITHOUT ROWID')
            db.execute('CREATE TABLE targets(parcel_key TEXT PRIMARY KEY) WITHOUT ROWID')
            db.executemany('INSERT INTO targets VALUES (?)', [(k,) for k in sorted(keys)])
            incomplete = set()
            for feed in HEADERS:
                count = selected = 0
                matched = set()
                with closing(sqlite3.connect(str(Path(tmp) / f'{feed}.sqlite'))) as seen:
                    seen.execute('CREATE TABLE keys(parcel TEXT,line INTEGER,PRIMARY KEY(parcel,line)) WITHOUT ROWID')
                    for row, raw in rows(feed, source_dir / f'{feed}.zip'):
                        key, line = identity(feed, row)
                        try:
                            seen.execute('INSERT INTO keys VALUES (?,?)', (key, line))
                        except sqlite3.IntegrityError:
                            raise ValueError('Duplicate canonical source key') from None
                        count += 1
                        if key in keys:
                            db.execute('INSERT INTO records VALUES (?,?,?,?,?,?)',
                                       (feed, key, line, json.dumps(row, ensure_ascii=False), raw, row_digest(feed, row)))
                            selected += 1
                            matched.add(key)
                        if count % 100000 == 0:
                            seen.commit()
                            db.commit()
                report['feeds'][feed] = {'source_sha256': sources[feed]['sha256'], 'source_rows': count,
                                         'selected_rows': selected, 'matched_targets': len(matched),
                                         'missing_targets': len(keys - matched), 'header': HEADERS[feed]}
                if count != sources[feed]['rows'] or digest(source_dir / f'{feed}.zip') != sources[feed]['sha256']:
                    raise ValueError('Source changed or row count differs')
                if feed == 'parcel' and matched != keys:
                    raise ValueError(f'{feed} target coverage incomplete')
                incomplete.update(keys - matched)
                fingerprints = ''.join(r[0] for r in db.execute('SELECT field_md5 FROM records WHERE feed=? ORDER BY parcel_key,line', (feed,)))
                report['feeds'][feed]['selected_fields_md5'] = hashlib.md5(fingerprints.encode()).hexdigest()
            db.commit()
            report['profiles_requiring_review'] = len(incomplete)
            report['complete_profiles'] = len(keys - incomplete)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity failed')
        final = output / 'profiles.sqlite'
        (output / 'profiles.partial.sqlite').rename(final)
        report.update(status='READY', snapshot_sha256=digest(final), snapshot_bytes=final.stat().st_size)
        return report
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        (output / 'manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--targets', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.source_dir, json.loads(a.sources.read_text()),
                          json.loads(a.targets.read_text()), a.output), indent=2))


if __name__ == '__main__':
    main()
