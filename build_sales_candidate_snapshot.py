"""Versioned, private offline sales screening. No valuation or production writes."""
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
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from build_target_tax_snapshot import digest, target_keys
from property_profile import source_review_flags
from property_source import HEADERS, identity, rows as property_rows
from sales_contract import HEADER, normalize

CONTRACT = 'polk-sales-candidate-screen-v2'
OBSERVED_CANDIDATE_CODES = {
    '01': 'Q-Per examination of deed',
    '02': 'Q-Credible, verified & documented',
}
REMAINING_REVIEW = [
    'historical_export_semantics_unverified',
    'transaction_identity_unverified',
    'sale_date_property_attributes_unverified',
    'subject_comparability_unverified',
]


def reference(value):
    """Conservative numeric alias grouping; never assert a deed identity."""
    value = value.strip()
    if re.fullmatch('[0-9]+', value) and int(value) > 0:
        return str(int(value))
    return ''


def screen(row, parcel, recording_group, sale_id_group):
    reasons = []
    code, description = row[10].strip(), row[11].strip()
    if code not in OBSERVED_CANDIDATE_CODES:
        reasons.append('qualification_code_outside_candidate_contract')
    elif description != OBSERVED_CANDIDATE_CODES[code]:
        reasons.append('qualification_code_description_drift')
    if not row[4]:
        reasons.append('price_missing')
    elif Decimal(row[4]) <= 100:
        reasons.append('nominal_price_screen_at_most_100')
    if row[7].strip() not in ('I', 'V'):
        reasons.append('sale_type_unrecognized')
    if row[14].strip() != 'N':
        reasons.append('foreclosure_flag_or_unknown')
    if not reference(row[5]) or not reference(row[6]):
        reasons.append('recording_reference_missing_or_unrecognized')
    if not reference(row[1]):
        reasons.append('sale_id_missing_or_unrecognized')
    if recording_group and recording_group[0] > 1:
        reasons.append('recording_reference_repeated')
    if recording_group and recording_group[1] != recording_group[2]:
        reasons.append('recording_spans_multiple_parcels')
    # Actual history reuses SALE_ID across many parcels. It is not a global deed
    # key. Keep the observation, without declaring those rows a package sale.
    identity_observations = []
    if sale_id_group and sale_id_group[0] > 1:
        identity_observations.append('sale_id_reference_repeated')
    if sale_id_group and sale_id_group[1] != sale_id_group[2]:
        identity_observations.append('sale_id_not_globally_unique')
    flags = source_review_flags([parcel]) if parcel else []
    if parcel is None:
        reasons.append('current_parcel_missing')
    else:
        if not all(parcel[field].strip() for field in ('DORUS_CODE', 'DORDESC', 'DORDESC1')):
            reasons.append('current_classification_incomplete')
        reasons.extend(flag['reason'] for flag in flags)
    return {
        'screen_status': 'held_for_review' if reasons else 'candidate_for_comparability_review',
        'screen_reasons': sorted(set(reasons)),
        'remaining_review': REMAINING_REVIEW,
        'current_parcel': parcel,
        'source_review_flags': flags,
        'identity_observations': identity_observations,
        'review_required': True,
        'qualified_comp': False,
        'eligible_for_automated_acquisition': False,
    }


def verify_input(path, fingerprint, count):
    if not re.fullmatch('[a-f0-9]{64}', fingerprint) or count < 1:
        raise ValueError('Require accepted SHA-256 and positive source row count')
    if digest(path) != fingerprint:
        raise ValueError('Source fingerprint differs from accepted snapshot')


def build(sales_zip, parcel_zip, target_export, output, *, sales_sha256,
          parcel_sha256, sales_rows, parcel_rows, start, as_of):
    start, as_of = date.fromisoformat(start), date.fromisoformat(as_of)
    if start > as_of or as_of > datetime.now(timezone.utc).date():
        raise ValueError('Invalid explicit sales date window')
    targets = target_keys(target_export)
    sales_zip, parcel_zip, output = Path(sales_zip), Path(parcel_zip), Path(output)
    verify_input(sales_zip, sales_sha256, sales_rows)
    verify_input(parcel_zip, parcel_sha256, parcel_rows)
    output.mkdir(parents=True, exist_ok=False)
    report = {
        'status': 'BLOCKED', 'contract': CONTRACT, 'mode': 'private_offline_screen',
        'built_at': datetime.now(timezone.utc).isoformat(),
        'start_inclusive': start.isoformat(), 'as_of_inclusive': as_of.isoformat(),
        'sales_sha256': sales_sha256, 'parcel_sha256': parcel_sha256,
        'target_captured_at': target_export['captured_at'], 'target_count': len(targets),
        'target_export_sha256': hashlib.sha256(
            json.dumps(target_export, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'grouping_scope': 'all_rows_in_accepted_sales_archive',
        'reference_normalization': 'positive_ascii_integer_strip_leading_zeros',
        'nominal_price_screen': 'price <= 100 USD is held; larger prices are not validated values',
        'observed_candidate_codes': OBSERVED_CANDIDATE_CODES,
        'remaining_review': REMAINING_REVIEW,
        'qualified_comps': False, 'production_verified': False,
        'read_cutover_allowed': False, 'eligible_for_automated_acquisition': False,
        'destructive_cleanup_enabled': False, 'durable_remote_archive_verified': False,
    }
    try:
        for source, name, expected in [(sales_zip, 'sales.zip', sales_sha256),
                                        (parcel_zip, 'parcel.zip', parcel_sha256)]:
            shutil.copyfile(source, output / name)
            verify_input(output / name, expected, 1)
        dimension = {}
        for row, _ in property_rows('parcel', output / 'parcel.zip'):
            key, _ = identity('parcel', row)
            if key in dimension:
                raise ValueError('Duplicate current parcel key')
            dimension[key] = {field: row[index] for index, field in enumerate(HEADERS['parcel'])
                              if field in ('PARCEL_ID', 'DORUS_CODE', 'DORDESC', 'DORDESC1')}
        if len(dimension) != parcel_rows:
            raise ValueError('Current parcel row count mismatch')
        counts = Counter({key: 0 for key in (
            'source_rows', 'window_rows', 'undated_rows', 'after_as_of_rows', 'before_window_rows',
            'target_window_rows', 'non_target_window_rows', 'target_candidate_rows',
            'non_target_candidate_rows', 'held_for_review', 'candidate_for_comparability_review')})
        reasons, observations = Counter(), Counter()
        partial = output / 'sales_candidates.partial.sqlite'
        # This scratch directory is newly created beneath this new output only.
        with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as scratch, \
                closing(sqlite3.connect(Path(scratch) / 'all_keys.sqlite')) as keys, \
                closing(sqlite3.connect(partial)) as db:
            keys.execute('CREATE TABLE source_keys (parcel TEXT, line INTEGER, sale_id TEXT, book TEXT, page TEXT, PRIMARY KEY(parcel,line)) WITHOUT ROWID')
            db.execute('CREATE TABLE sales (parcel TEXT, line INTEGER, payload TEXT NOT NULL, is_target INTEGER NOT NULL, assessment TEXT, PRIMARY KEY(parcel,line)) WITHOUT ROWID')
            with zipfile.ZipFile(output / 'sales.zip') as archive:
                members = [m for m in archive.infolist() if not m.is_dir()]
                if len(members) != 1:
                    raise ValueError('Expected one sales archive member')
                with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', errors='strict', newline='') as stream:
                    reader = csv.reader(stream, strict=True)
                    if next(reader) != HEADER:
                        raise ValueError('Sales header drift')
                    for source_row in reader:
                        row = normalize(source_row)  # Validate even excluded historical rows.
                        keys.execute('INSERT INTO source_keys VALUES (?,?,?,?,?)',
                                     (row[0], int(row[2]), reference(row[1]), reference(row[5]), reference(row[6])))
                        counts['source_rows'] += 1
                        sale_date = datetime.strptime(row[3], '%m/%d/%Y').date() if row[3] else None
                        if sale_date is None:
                            counts['undated_rows'] += 1
                        elif sale_date > as_of:
                            counts['after_as_of_rows'] += 1
                        elif sale_date < start:
                            counts['before_window_rows'] += 1
                        else:
                            db.execute('INSERT INTO sales VALUES (?,?,?,?,NULL)',
                                       (row[0], int(row[2]), json.dumps(row, ensure_ascii=False), int(row[0] in targets)))
                            counts['window_rows'] += 1
                        if counts['source_rows'] % 100000 == 0:
                            keys.commit()
                            db.commit()
            if counts['source_rows'] != sales_rows:
                raise ValueError('Sales source row count mismatch')
            keys.commit()
            # MIN/MAX detects distinct parcel membership, including outside the window.
            keys.execute("CREATE TABLE recording_groups AS SELECT book,page,count(*) n,min(parcel) lo,max(parcel) hi FROM source_keys WHERE book<>'' AND page<>'' GROUP BY book,page HAVING count(*)>1")
            keys.execute('CREATE UNIQUE INDEX recording_lookup ON recording_groups(book,page)')
            keys.execute("CREATE TABLE sale_id_groups AS SELECT sale_id,count(*) n,min(parcel) lo,max(parcel) hi FROM source_keys WHERE sale_id<>'' GROUP BY sale_id HAVING count(*)>1")
            keys.execute('CREATE UNIQUE INDEX sale_id_lookup ON sale_id_groups(sale_id)')
            for parcel, line, payload, is_target in db.execute('SELECT parcel,line,payload,is_target FROM sales'):
                row = json.loads(payload)
                recording = keys.execute('SELECT n,lo,hi FROM recording_groups WHERE book=? AND page=?', (reference(row[5]), reference(row[6]))).fetchone()
                sale_group = keys.execute('SELECT n,lo,hi FROM sale_id_groups WHERE sale_id=?', (reference(row[1]),)).fetchone()
                assessment = screen(row, dimension.get(parcel), recording, sale_group)
                db.execute('UPDATE sales SET assessment=? WHERE parcel=? AND line=?',
                           (json.dumps(assessment, ensure_ascii=False), parcel, line))
                counts[assessment['screen_status']] += 1
                counts['target_window_rows' if is_target else 'non_target_window_rows'] += 1
                if not assessment['screen_reasons']:
                    counts['target_candidate_rows' if is_target else 'non_target_candidate_rows'] += 1
                reasons.update(assessment['screen_reasons'])
                observations.update(assessment['identity_observations'])
            db.commit()
            if db.execute('SELECT count(*) FROM sales WHERE assessment IS NULL').fetchone()[0]:
                raise ValueError('Missing row assessments')
            if db.execute('SELECT count(*) FROM sales').fetchone()[0] != counts['window_rows']:
                raise ValueError('Window count mismatch')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity check failed')
            report['full_history_multi_parcel_groups'] = {
                name: keys.execute(f'SELECT count(*) FROM {table} WHERE lo<>hi').fetchone()[0]
                for name, table in [('recording', 'recording_groups'), ('sale_id', 'sale_id_groups')]}
        for path, fingerprint in [(sales_zip, sales_sha256), (parcel_zip, parcel_sha256),
                                   (output / 'sales.zip', sales_sha256), (output / 'parcel.zip', parcel_sha256)]:
            verify_input(path, fingerprint, 1)
        target_keys(target_export)  # No READY result if the target export expired during the build.
        final = output / 'sales_candidates.sqlite'
        partial.rename(final)
        report.update(status='READY', counts=dict(counts), reason_counts=dict(sorted(reasons.items())),
                      identity_observation_counts=dict(sorted(observations.items())),
                      parcel_rows=len(dimension), snapshot_sha256=digest(final),
                      snapshot_bytes=final.stat().st_size, archive_local_verified=True)
        return report
    finally:
        (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('sales-zip', 'parcel-zip', 'target-export', 'output', 'sales-sha256',
                'parcel-sha256', 'start', 'as-of'):
        parser.add_argument('--' + arg, required=True)
    parser.add_argument('--sales-rows', type=int, required=True)
    parser.add_argument('--parcel-rows', type=int, required=True)
    args = vars(parser.parse_args())
    args['target_export'] = json.loads(Path(args['target_export']).read_text(encoding='utf-8'))
    result = build(**args)
    print(json.dumps({'status': result['status'], 'counts': result['counts'],
                      'qualified_comps': False, 'production_verified': False}))
