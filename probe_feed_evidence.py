#!/usr/bin/env python3
"""Read-only county feed inspection. No database credentials or production writes."""
import argparse
import csv
import hashlib
import io
import json
import sqlite3
import ssl
import tempfile
import zipfile
from datetime import datetime, timezone
from contextlib import closing
from ftplib import FTP_TLS
from pathlib import Path

HOST = 'ftp.polkflpa.gov'
FEEDS = ('parceltax', 'permit', 'sales')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(feed, path):
    remote = f'/AppraisalData/ftp_{feed}.zip'
    with FTP_TLS(HOST, timeout=60, context=ssl.create_default_context()) as ftp:
        ftp.login()
        ftp.prot_p()
        ftp.voidcmd('TYPE I')
        before = (ftp.size(remote), ftp.sendcmd('MDTM ' + remote))
        print(f'Downloading {remote}: {before[0]} bytes', flush=True)
        with path.open('xb') as out:
            ftp.retrbinary('RETR ' + remote, out.write, blocksize=1024 * 1024)
        after = (ftp.size(remote), ftp.sendcmd('MDTM ' + remote))
    if before != after or path.stat().st_size != before[0]:
        raise ValueError('Source changed during transfer or download size mismatch')
    return {'host': HOST, 'remote': remote, 'size_bytes': before[0],
            'remote_mdtm': before[1], 'transport': 'FTPS with certificate verification'}


def checked_rows(reader):
    try:
        yield from reader
    except csv.Error as exc:
        raise ValueError(f'Invalid CSV at physical line {reader.line_num}: {exc}') from exc


def inspect(path, encoding='utf-8-sig', report=None):
    report = {} if report is None else report
    report.update({'archive_sha256': sha256(path), 'archive_bytes': path.stat().st_size,
              'encoding': encoding, 'production_verified': False,
              'parcel_inventory_reconciled': False})
    with tempfile.TemporaryDirectory(prefix='polk_probe_') as tmp, \
            closing(sqlite3.connect(str(Path(tmp) / 'keys.sqlite'))) as db, zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) != 1:
            raise ValueError('Expected exactly one data member')
        member = members[0]
        report.update(member=member.filename, uncompressed_bytes=member.file_size)
        with archive.open(member) as raw, io.TextIOWrapper(raw, encoding=encoding, errors='strict', newline='') as stream:
            first = stream.readline()
            delimiter = csv.Sniffer().sniff(first, delimiters=',\t|;').delimiter
            stream.seek(0)
            reader = csv.reader(stream, delimiter=delimiter, strict=True)
            header = next(reader)
            if not header or any(not h.strip() for h in header) or len(header) != len(set(header)):
                raise ValueError('Empty or duplicate column names')
            if 'PARCEL_ID' not in header:
                raise ValueError('Missing PARCEL_ID; no parcel-key validation possible')
            report.update(header=header, delimiter=delimiter,
                          header_sha256=hashlib.sha256(json.dumps(header).encode()).hexdigest())
            candidates = [('PARCEL_ID',)] + [('PARCEL_ID', h) for h in
                ('LNNUM', 'LN_NUM', 'NUM', 'ID', 'TAXDIST', 'PERMIT_ID', 'PERMIT_NUM', 'PERMITNO') if h in header]
            indices = [[header.index(h) for h in key] for key in candidates]
            stats = [{'columns': list(key), 'blank_rows': 0, 'duplicate_rows': 0} for key in candidates]
            for i in range(len(candidates)):
                db.execute(f'CREATE TABLE k{i} (value TEXT PRIMARY KEY) WITHOUT ROWID')
            batches = [[] for _ in candidates]

            def flush():
                for i, batch in enumerate(batches):
                    before = db.total_changes
                    db.executemany(f'INSERT OR IGNORE INTO k{i} VALUES (?)', batch)
                    stats[i]['duplicate_rows'] += len(batch) - (db.total_changes - before)
                    batch.clear()
                db.commit()

            rows = bad_width = 0
            blanks = [0] * len(header)
            whitespace = [0] * len(header)
            max_lengths = [0] * len(header)
            for row in checked_rows(reader):
                rows += 1
                if len(row) != len(header):
                    bad_width += 1
                    continue
                for i, value in enumerate(row):
                    blanks[i] += not value.strip()
                    whitespace[i] += value != value.strip()
                    max_lengths[i] = max(max_lengths[i], len(value))
                for i, cols in enumerate(indices):
                    key = tuple(row[c].strip() for c in cols)
                    if any(not value for value in key):
                        stats[i]['blank_rows'] += 1
                    else:
                        batches[i].append((json.dumps(key, ensure_ascii=False),))
                if rows % 10000 == 0:
                    flush()
                if rows % 500000 == 0:
                    print(f'Inspected {rows:,} rows', flush=True)
            flush()
        for i, stat in enumerate(stats):
            stat['distinct_nonblank_keys'] = db.execute(f'SELECT count(*) FROM k{i}').fetchone()[0]
            stat['unique_complete_key'] = rows > 0 and bad_width == 0 and stat['blank_rows'] == 0 and stat['duplicate_rows'] == 0
        report.update(rows=rows, bad_width_rows=bad_width, keys=stats,
                      columns={h: {'blank_rows': blanks[i], 'outer_whitespace_rows': whitespace[i],
                                   'max_characters': max_lengths[i]} for i, h in enumerate(header)})
        report['structural_validation_passed'] = rows > 0 and bad_width == 0 and stats[0]['blank_rows'] == 0
        report['schema_candidate_ready'] = report['structural_validation_passed'] and any(s['unique_complete_key'] for s in stats)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feed', choices=FEEDS)
    parser.add_argument('--zip', type=Path, help='Inspect an existing archive; do not download')
    parser.add_argument('--output-dir', type=Path, default=Path('.polk_import/evidence'))
    parser.add_argument('--encoding', default='utf-8-sig', help='Strict decoding; never replaces invalid bytes')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = args.zip or args.output_dir / f'{args.feed}-{stamp}.zip'
    report = {'feed': args.feed, 'started_at': stamp, 'production_verified': False, 'status': 'BLOCKED'}
    try:
        report['source'] = download(args.feed, path) if not args.zip else {'local_archive': str(path.resolve()), 'official_origin_verified': False}
        inspect(path, args.encoding, report)
        report['status'] = 'READY' if report['schema_candidate_ready'] else 'BLOCKED'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        output = args.output_dir / f'{args.feed}-{stamp}.json'
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Evidence: {output.resolve()}', flush=True)
    if not report['schema_candidate_ready']:
        raise SystemExit('No complete unique candidate key; schema remains blocked')


if __name__ == '__main__':
    main()
