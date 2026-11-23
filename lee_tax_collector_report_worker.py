#!/usr/bin/env python3
"""Stage official Lee County GovHub real-estate tax CSVs for provenance review.

The previously configured leetc.com/api/parcel/delinquency path is undocumented.
Obtain an authorized real tax report from https://county-taxes.net/fl-lee/reports;
normalize its columns to: parcel_id,tax_year,tax_status,amount_due.
No tax evidence is promoted, and no leads or screening statuses are modified.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, unquote

STAGING_PROJECT = 'ltwqgkcblvtscgldxvjw'
OFFICIAL_REPORT_URL = 'https://county-taxes.net/fl-lee/reports'
VALID_STATUSES = {'PAID','UNPAID','DELINQUENT','CERTIFICATE','REDEEMED','UNKNOWN'}

class TaxReportError(ValueError):
    """Invalid, unverifiable, or ambiguous input."""

def allowed_source_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.hostname == 'county-taxes.net'
                and p.path == '/fl-lee/reports'
                and not p.username and not p.password and not p.query and not p.fragment)
    except (ValueError, TypeError):
        return False

def normalize_strap(parcel_id: str) -> str:
    cleaned = re.sub(r'[-.\s]', '', str(parcel_id)).upper()
    if not re.fullmatch(r'[A-Z0-9]{17}', cleaned):
        raise TaxReportError('Invalid Lee County STRAP: require exactly 17 alphanumeric characters')
    return cleaned

def staging_dsn_is_safe(dsn: str) -> bool:
    try:
        p = urlsplit(dsn)
        if p.scheme not in {'postgres','postgresql'} or not p.hostname:
            return False
        host, user = p.hostname.lower(), unquote(p.username or '')
        return host == f'db.{STAGING_PROJECT}.supabase.co' or (
            host.endswith('.pooler.supabase.com') and user == f'postgres.{STAGING_PROJECT}')
    except (TypeError, ValueError):
        return False

def interpret_tax_status(raw: str, tax_year: int, observed: date):
    status = str(raw).strip().upper()
    if status not in VALID_STATUSES:
        raise TaxReportError('Unrecognized report status')
    if tax_year < 2000 or tax_year > observed.year:
        raise TaxReportError('Unsupported report tax year')
    if status == 'DELINQUENT' and observed < date(tax_year+1,4,1):
        raise TaxReportError('A tax bill is not delinquent before April 1 of following year')
    # Claimed status remains unverified until official report and current payment history are reconciled.
    return status, status == 'DELINQUENT'

def parse_report_csv(source, observed: date):
    reader = csv.DictReader(source)
    needed = {'parcel_id','tax_year','tax_status','amount_due'}
    if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)) or not needed.issubset(reader.fieldnames):
        raise TaxReportError('CSV requires unique parcel_id,tax_year,tax_status,amount_due columns')
    records=[]; seen=set()
    for number,row in enumerate(reader,start=2):
        if number>100002:
            raise TaxReportError('CSV exceeds 100k rows')
        try:
            parcel_id = str(row['parcel_id']).strip()
            strap = normalize_strap(parcel_id)
            year = int(row['tax_year'])
            status,claimed = interpret_tax_status(row['tax_status'],year,observed)
            raw_amount = (row['amount_due'] or '').strip().replace(',','')
            amount = None if not raw_amount else Decimal(raw_amount)
            if amount is not None and (not amount.is_finite() or amount<0 or amount.as_tuple().exponent < -2):
                raise TaxReportError('Negative or invalid amount')
        except (KeyError,ValueError,TypeError,InvalidOperation,TaxReportError) as exc:
            raise TaxReportError(f'Invalid CSV row {number}: {type(exc).__name__}') from exc
        key=(strap,year)
        if key in seen:
            raise TaxReportError(f'Duplicate parcel and tax year at row {number}')
        seen.add(key)
        records.append({'parcel_id':parcel_id,'normalized_strap':strap,
                        'tax_year':year,'tax_status':status,
                        'amount_due':str(amount) if amount is not None else None,
                        'delinquent_claimed':claimed})
    if not records:
        raise TaxReportError('Report is empty')
    return records

def read_report(path: Path, observed: date):
    if not path.is_file() or path.stat().st_size>25_000_000:
        raise TaxReportError('Missing report or file over 25MB')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open(encoding='utf-8-sig',newline='') as src:
        rows=parse_report_csv(src,observed)
    return digest,rows

def insert_report(conn,digest,report_date,rows):
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO god_mode_ops.lee_tax_report_import_batches
          (source_url,report_generated_date,archive_sha256,row_count,validation_status)
          VALUES (%s,%s,%s,%s,'PENDING_PROVENANCE_REVIEW')
          ON CONFLICT(archive_sha256) DO UPDATE SET source_url=excluded.source_url
          RETURNING batch_id""",(OFFICIAL_REPORT_URL,report_date,digest,len(rows)))
        batch_id=cur.fetchone()[0]
        values=[(batch_id,r['parcel_id'],r['normalized_strap'],r['tax_year'],
                 r['tax_status'],r['amount_due'],r['delinquent_claimed']) for r in rows]
        execute_values(cur,"""INSERT INTO god_mode_ops.lee_tax_report_import_observations
          (batch_id,parcel_id,normalized_strap,tax_year,reported_status,amount_due,delinquent_claimed)
          SELECT v.batch_id,s.parcel_id,v.normalized_strap,v.tax_year,v.reported_status,v.amount_due,v.delinquent_claimed
          FROM (VALUES %s) AS v(batch_id,parcel_id,normalized_strap,tax_year,reported_status,amount_due,delinquent_claimed)
          JOIN god_mode_ops.lee_parcel_qualification_screening s
            ON regexp_replace(upper(s.parcel_id),'[-.[:space:]]','','g')=v.normalized_strap
           AND s.county='Lee County'
          ON CONFLICT(batch_id,parcel_id,tax_year) DO NOTHING""",
          values,template="(%s::uuid,%s,%s,%s,%s,%s::numeric,%s)")
        cur.execute("SELECT count(*) FROM god_mode_ops.lee_tax_report_import_observations WHERE batch_id=%s",(batch_id,))
        matched=cur.fetchone()[0]
    conn.commit()
    return batch_id,matched

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv',type=Path,required=True)
    p.add_argument('--report-date',type=date.fromisoformat,required=True)
    p.add_argument('--source-url',default=OFFICIAL_REPORT_URL)
    p.add_argument('--confirm-official-export',action='store_true')
    p.add_argument('--write',action='store_true')
    a=p.parse_args(argv)
    if not allowed_source_url(a.source_url):
        p.error('Unverified tax report source URL')
    if a.report_date>date.today():
        p.error('Future report generation date')
    digest,rows=read_report(a.csv,a.report_date)
    print(f'SOURCE INPUT: CSV rows={len(rows)} SHA256={digest}; no qualification or delinquency promotion')
    if not a.write:
        print('DRY RUN: no database writes')
        return 0
    if not a.confirm_official_export:
        p.error('Attest the file came from an authorized official Lee County GovHub export')
    import os
    dsn=os.getenv('STAGING_SUPABASE_DB_URL','')
    if not staging_dsn_is_safe(dsn):
        raise TaxReportError('Dedicated Supabase staging-only database URL required')
    import psycopg2
    conn=psycopg2.connect(dsn,connect_timeout=12,
        sslmode='verify-full' if os.getenv('PGSSLROOTCERT') else 'require')
    try:
        batch,matched=insert_report(conn,digest,a.report_date,rows)
        print(f'STAGED FOR REVIEW: matched parcels={matched} batch={batch}, no live tax claims')
        return 0
    finally:
        conn.close()

if __name__=='__main__':
    try:sys.exit(main())
    except TaxReportError as e:
        print('TAX REPORT BLOCKED:',e,file=sys.stderr);sys.exit(2)
