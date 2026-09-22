"""Build a private, offline parcel-tax shadow snapshot. Never writes to PostgreSQL."""
import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

HEADER = ['PARCEL_ID', 'LNNUM', 'TAXDIST', 'DISTNAME', 'DISTDESC', 'ASDVAL',
          'EXMPTVAL', 'TAXVAL', 'ASDTAXES', 'EXMPTTAXES', 'TAXESDUE']
PROJECT = 'bnsmnztxkqmphvbikaxh'


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def parcel_key(value):
    key = re.sub('[^A-Z0-9]', '', value.upper())
    if not re.fullmatch('[0-9]{18}', key):
        raise ValueError('Invalid normalized parcel key')
    return key


def target_keys(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    captured = datetime.fromisoformat(snapshot['captured_at'])
    if captured.tzinfo is None or not timedelta(0) <= now - captured <= timedelta(hours=24):
        raise ValueError('Target snapshot must be timezone-aware and at most 24 hours old')
    if snapshot.get('project_ref') != PROJECT or snapshot.get('registry_drift') != 0:
        raise ValueError('Target registry provenance or equivalence failed')
    raw = snapshot['parcel_keys']
    if not raw or any(not isinstance(k, str) or not re.fullmatch('[0-9]{18}', k) for k in raw):
        raise ValueError('Target export contains invalid keys')
    keys = set(raw)
    if len(keys) != len(raw):
        raise ValueError('Duplicate target keys')
    return keys


def validate_row(row):
    if len(row) != len(HEADER) or any('\x00' in v for v in row):
        raise ValueError('Invalid parcel-tax row width or NUL')
    key = parcel_key(row[0])
    if not re.fullmatch('[0-9]+', row[1]) or int(row[1]) > 2147483647:
        raise ValueError('Invalid LNNUM')
    if not row[2].strip():
        raise ValueError('Missing TAXDIST')
    for value in row[5:]:
        if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]{1,2})?|\.[0-9]{1,2})', value) or Decimal(value) < 0:
            raise ValueError('Invalid nonnegative tax amount')
    return key, int(row[1])


def build(archive, snapshot, output, accepted_sha256, expected_rows):
    archive, output = Path(archive), Path(output)
    keys = target_keys(snapshot)
    if expected_rows < 1 or not re.fullmatch('[a-f0-9]{64}', accepted_sha256):
        raise ValueError('Require accepted archive SHA-256 and positive row count')
    if digest(archive) != accepted_sha256:
        raise ValueError('Source fingerprint differs from accepted snapshot')
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'BLOCKED', 'feed': 'parcel_tax', 'mode': 'target_context_shadow',
              'production_verified': False, 'durable_remote_archive_verified': False,
              'destructive_cleanup_enabled': False, 'tax_delinquency_inferred': False,
              'source_sha256': accepted_sha256, 'source_bytes': archive.stat().st_size,
              'target_count': len(keys), 'target_captured_at': snapshot['captured_at'],
              'target_keys_sha256': hashlib.sha256('\n'.join(sorted(keys)).encode()).hexdigest()}
    try:
        retained = output / (accepted_sha256 + '.zip')
        shutil.copyfile(archive, retained)
        if digest(retained) != accepted_sha256:
            raise ValueError('Retained archive integrity check failed')
        matched = set()
        total = selected = 0
        partial = output / 'target_tax.partial.sqlite'
        with tempfile.TemporaryDirectory(prefix='polk_tax_keys_') as tmp, \
                closing(sqlite3.connect(str(Path(tmp) / 'keys.sqlite'))) as seen, \
                closing(sqlite3.connect(partial)) as db, zipfile.ZipFile(retained) as source:
            seen.execute('CREATE TABLE source_keys (parcel TEXT, line INTEGER, PRIMARY KEY(parcel,line)) WITHOUT ROWID')
            db.execute('CREATE TABLE target_tax (parcel_key TEXT, line INTEGER, payload TEXT NOT NULL, PRIMARY KEY(parcel_key,line)) WITHOUT ROWID')
            members = [m for m in source.infolist() if not m.is_dir()]
            if len(members) != 1:
                raise ValueError('Expected exactly one archive member')
            with source.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', errors='strict', newline='') as stream:
                first = stream.readline()
                delimiter = csv.Sniffer().sniff(first, delimiters=',\t|;').delimiter
                stream.seek(0)
                reader = csv.reader(stream, delimiter=delimiter, strict=True)
                if next(reader) != HEADER:
                    raise ValueError('Parcel-tax header differs from observed contract')
                for row in reader:
                    key, line = validate_row(row)  # Validate ALL rows before filtering.
                    try:
                        seen.execute('INSERT INTO source_keys VALUES (?,?)', (key, line))
                    except sqlite3.IntegrityError:
                        raise ValueError('Duplicate normalized source key') from None
                    total += 1
                    if key in keys:
                        db.execute('INSERT INTO target_tax VALUES (?,?,?)', (key, line, json.dumps(row, ensure_ascii=False)))
                        selected += 1
                        matched.add(key)
                    if total % 100000 == 0:
                        seen.commit()
                        db.commit()
                report.update(source_rows=total, selected_rows=selected, matched_targets=len(matched),
                              missing_targets=len(keys - matched), header=HEADER)
            if total != expected_rows:
                raise ValueError('Source row count differs from accepted snapshot')
            if keys != matched:
                raise ValueError('Target coverage incomplete; review missing targets before publication')
            db.commit()
            if db.execute('SELECT count(*) FROM target_tax').fetchone()[0] != selected:
                raise ValueError('Local snapshot count mismatch')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Local snapshot integrity check failed')
        if digest(retained) != accepted_sha256:
            raise ValueError('Archive changed during processing')
        final = output / 'target_tax.sqlite'
        partial.rename(final)
        report.update(status='READY', archive_local_verified=True, snapshot_bytes=final.stat().st_size,
                      snapshot_sha256=digest(final), retained_fraction=selected / total)
        return report
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', required=True, type=Path)
    parser.add_argument('--targets', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New private output directory')
    parser.add_argument('--accepted-sha256', required=True)
    parser.add_argument('--expected-rows', required=True, type=int)
    args = parser.parse_args()
    report = build(args.zip, json.loads(args.targets.read_text(encoding='utf-8')), args.output,
                   args.accepted_sha256, args.expected_rows)
    print(json.dumps(report, indent=2))  # Aggregate evidence only; no property data.


if __name__ == '__main__':
    main()
