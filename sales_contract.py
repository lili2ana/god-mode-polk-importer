"""Deterministic sales contract; no network or production writes."""
import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from decimal import Decimal

HEADER = ['PARCEL_ID','SALE_ID','LN_NUM','SALEDT','PRICE','BOOK','PAGE','SALETYPE','TRNS_CD','TRNS_DSCR','INSTRTYP','INSTRTYP_DSCR','GRANTOR','GRANTEE','FORECLOSURE']
COLUMNS = [h.lower() for h in HEADER]
PROJECT = 'bnsmnztxkqmphvbikaxh'


def normalize(row):
    if len(row) != 15:
        raise ValueError('Sales row width differs from 15-column contract')
    row = ['' if value is None else str(value) for value in row]
    if any('\x00' in value for value in row):
        raise ValueError('NUL byte cannot be represented by PostgreSQL text')
    row[0] = row[0].strip()
    if not re.fullmatch(r'[0-9]{18}', row[0]):
        raise ValueError('PARCEL_ID must be 18 ASCII digits')
    line = row[2].strip()
    if not re.fullmatch(r'[0-9]+', line) or int(line) > 2147483647:
        raise ValueError('LN_NUM must fit a nonnegative PostgreSQL integer')
    row[2] = str(int(line))
    row[3] = row[3].strip()
    if row[3]:
        if not re.fullmatch(r'[0-9]{2}/[0-9]{2}/[0-9]{4}', row[3]):
            raise ValueError('SALEDT must be MM/DD/YYYY or empty')
        datetime.strptime(row[3], '%m/%d/%Y')
    row[4] = row[4].strip()
    if row[4]:
        if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', row[4]):
            raise ValueError('PRICE must be a finite decimal or empty')
        if Decimal(row[4]) < 0:
            raise ValueError('Negative PRICE requires source review')
    return row


def typed(row):
    row = normalize(row)
    result = [value or None for value in row]
    result[2] = int(row[2])
    result[3] = datetime.strptime(row[3], '%m/%d/%Y').date() if row[3] else None
    result[4] = Decimal(row[4]) if row[4] else None
    return tuple(result)


def prepare(archive_path, database_path, expected_rows, partitions=32):
    if expected_rows < 1 or not 1 <= partitions <= 128:
        raise ValueError('Invalid row count or partition count')
    with archive_path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    db = sqlite3.connect(database_path)
    try:
        db.execute('CREATE TABLE expected (parcel TEXT, line INTEGER, shard INTEGER, payload TEXT, PRIMARY KEY(parcel,line)) WITHOUT ROWID')
        db.execute('CREATE TABLE seen (parcel TEXT, line INTEGER, PRIMARY KEY(parcel,line)) WITHOUT ROWID')
        rows = 0
        with zipfile.ZipFile(archive_path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) != 1:
                raise ValueError('Expected exactly one sales ZIP member')
            with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', errors='strict', newline='') as stream:
                reader = csv.reader(stream, strict=True)
                if next(reader) != HEADER:
                    raise ValueError('Sales header drift')
                for source_row in reader:
                    rows += 1
                    try:
                        row = normalize(source_row)
                        shard = int(hashlib.sha256(row[0].encode()).hexdigest()[:16], 16) % partitions
                        db.execute('INSERT INTO expected VALUES (?,?,?,?)', (row[0], int(row[2]), shard, json.dumps(row)))
                    except (ValueError, sqlite3.IntegrityError) as exc:
                        raise ValueError(f'Source row {rows} failed validation: {type(exc).__name__}') from exc
                    if rows % 10000 == 0:
                        db.commit()
                    if rows % 500000 == 0:
                        print(f'validated_rows={rows}', flush=True)
        if rows != expected_rows:
            raise ValueError(f'Snapshot row count mismatch: expected {expected_rows}, got {rows}')
        db.execute('CREATE INDEX expected_shard ON expected(shard)')
        db.commit()
        return {'sha256': digest, 'rows': rows, 'partitions': partitions,
                'archive_bytes': archive_path.stat().st_size, 'member': members[0].filename,
                'header': HEADER, 'production_verified': False}
    finally:
        db.close()


def reconcile(db, rows, production=False):
    """Every incoming key/value must match source; duplicates are never ignored."""
    db.execute('DELETE FROM seen')
    count = 0
    for incoming in rows:
        if production:
            key = incoming[0], incoming[2]
        else:
            incoming = normalize(incoming)
            key = incoming[0], int(incoming[2])
        expected = db.execute('SELECT payload FROM expected WHERE parcel=? AND line=?', key).fetchone()
        if expected is None:
            raise ValueError('Database contains a key outside the accepted source snapshot')
        source = json.loads(expected[0])
        matches = tuple(incoming) == typed(source) if production else incoming == source
        if not matches:
            raise ValueError('Database field values differ from the accepted source snapshot')
        try:
            db.execute('INSERT INTO seen VALUES (?,?)', key)
        except sqlite3.IntegrityError as exc:
            raise ValueError('Duplicate canonical database key') from exc
        count += 1
    db.commit()
    return count


def validate_target(dsn):
    from psycopg2.extensions import parse_dsn
    parts = parse_dsn(dsn)
    host = parts.get('host', '')
    direct = host == f'db.{PROJECT}.supabase.co' and parts.get('user') == 'postgres'
    pooler = host.endswith('.pooler.supabase.com') and parts.get('user') == f'postgres.{PROJECT}'
    if not (direct or pooler) or parts.get('dbname') != 'postgres' or parts.get('hostaddr'):
        raise ValueError('Database target does not match the configured Polk project')
    if parts.get('port', '5432') != '5432':
        raise ValueError('Use direct or session-pooler port 5432 for session locks and COPY')
    if parts.get('sslmode', 'require') not in ('require', 'verify-ca', 'verify-full'):
        raise ValueError('Database TLS must be enabled')
    return parts


def require_writable(cursor):
    cursor.execute("SELECT current_setting('transaction_read_only'), pg_is_in_recovery(), current_database()")
    if cursor.fetchone() != ('off', False, 'postgres'):
        raise RuntimeError('Database readiness gate failed; no writes allowed')
