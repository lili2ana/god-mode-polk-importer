"""Normalize an authorized Lee TaxSys report snapshot; never approve distress."""
import argparse
import calendar
from collections import Counter
import datetime
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse


def candidates(snapshot, today=None):
    today = today or datetime.date.today()
    source = urlparse(snapshot.get('source_url', ''))
    if source.scheme != 'https' or source.hostname != 'county-taxes.net' or source.username or source.password:
        raise ValueError('Expected official Lee TaxSys source')
    if not source.path.startswith('/fl-lee/'):
        raise ValueError('Expected Lee County report')
    checked = datetime.datetime.fromisoformat(snapshot['checked_at'])
    if checked.tzinfo is None:
        raise ValueError('Expected timezone-aware retrieval time')
    rows = snapshot.get('rows', [])
    if snapshot.get('complete') is not True or len(rows) != snapshot.get('reported_total'):
        raise ValueError('Incomplete report; count does not reconcile')
    accepted, excluded = [], {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 20:
            raise ValueError('Unexpected report layout')
    identities = Counter((re.sub('[^A-Z0-9]', '', r[1].upper()), r[0], r[4]) for r in rows)
    for row in rows:
        if not isinstance(row, list) or len(row) != 20:
            raise ValueError('Unexpected report layout')
        try:
            year = int(row[0])
            due = Decimal(row[19].replace(',', ''))
        except (ValueError, InvalidOperation):
            raise ValueError('Invalid tax year or balance') from None
        parcel = re.sub('[^A-Z0-9]', '', row[1].upper())
        since = datetime.date(year + 1, 4, 1)
        cutoff = today.replace(year=today.year - 1, day=min(today.day, calendar.monthrange(today.year-1, today.month)[1]))
        reason = None
        if not re.fullmatch(r'\d{6}[A-Z0-9]{11}', parcel): reason = 'INVALID_PARCEL'
        elif row[3] != 'Issued': reason = 'CERTIFICATE_NOT_ISSUED'
        elif row[2] != '-- None --': reason = 'DEED_STATUS_REVIEW'
        elif row[16] != 'Unpaid' or not due.is_finite() or due <= 0: reason = 'NO_UNPAID_BALANCE'
        elif since >= cutoff: reason = 'DELINQUENCY_NOT_OVER_12_MONTHS'
        elif not row[4].isdigit() or int(row[4]) <= 0: reason = 'MISSING_CERTIFICATE'
        identity = (parcel, year, row[4])
        if identities[(parcel, row[0], row[4])] > 1: reason = 'DUPLICATE_CERTIFICATE_REVIEW'
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        accepted.append({'schema_version': 1, 'parcel_id': parcel, 'county': 'Lee',
            'source_name': 'official_tax_collector', 'source_url': snapshot['source_url'],
            'checked_at': snapshot['checked_at'], 'source_record_id': row[4],
            'record_sha256': hashlib.sha256(json.dumps(row, separators=(',', ':')).encode()).hexdigest(),
            'signal_type': 'tax_certificate_delinquency', 'tax_year': year,
            'delinquent_since': since.isoformat(), 'amount_due': str(due),
            'reported_owner': row[6], 'site_address': row[5], 'use_code': row[17],
            'verification_status': 'REVIEW_REQUIRED', 'qualifying_distress': False,
            'release_search_complete': False, 'owner_match_verified': False,
            'certificate_status': row[3], 'deed_status': row[2]})
    return {'candidates': accepted, 'excluded': excluded, 'input_rows': len(rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('snapshot'); parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = candidates(json.loads(Path(args.snapshot).read_text()))
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps({'input_rows': result['input_rows'], 'candidates': len(result['candidates']),
                      'excluded': result['excluded'], 'auto_approved': 0}))
