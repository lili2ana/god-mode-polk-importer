#!/usr/bin/env python3
"""Retrieve a user-authorized Lee County GovHub *CSV export* and stage it for review.

No undocumented API calls, browser scraping, or login bypass. This worker requires
an actual official CSV export URL supplied in the LEE_GOVHUB_EXPORT_URL GitHub
Actions secret. Without that URL it stops. It never creates qualified leads.

The existing lee_tax_collector_report_worker.py handles strict normalized CSV
validation, source hashing, staging-only connections, and quarantined inserts.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import requests

class GovHubSourceError(ValueError):
    pass


def validate_govhub_export_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.fragment or p.port not in (None,443):
            return False
        if p.hostname == 'lee.county-taxes.com':
            return p.path.startswith('/') and p.path != '/'
        if p.hostname == 'county-taxes.net':
            return p.path.startswith('/fl-lee/')
        return False
    except (ValueError,TypeError):
        return False


def download_govhub_csv(session, url: str, dest: Path, max_bytes: int = 25_000_000) -> str:
    """Write only a validated non-HTML official CSV; return SHA256, not URL/token."""
    if not validate_govhub_export_url(url):
        raise GovHubSourceError('Export link host or protocol is not an allowed Lee GovHub source')
    response = session.get(url, stream=True, allow_redirects=True,
                           timeout=(15, 50), headers={'Accept':'text/csv,text/plain,application/octet-stream'})
    temp = dest.with_name(dest.name+'.incomplete')
    try:
        response.raise_for_status()
        for hop in [*response.history,response]:
            if not validate_govhub_export_url(hop.url):
                raise GovHubSourceError('Export redirect left official Lee GovHub hosts')
        content_type = response.headers.get('Content-Type','').split(';')[0].strip().lower()
        if content_type in {'text/html','application/xhtml+xml','application/json'}:
            raise GovHubSourceError('GovHub returned HTML, JSON, or login screen instead of a CSV export')
        declared = response.headers.get('Content-Length')
        if declared and int(declared)>max_bytes:
            raise GovHubSourceError('CSV export exceeds maximum size')
        dest.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256(); byte_count=0; head=b''
        with temp.open('wb') as fh:
            for part in response.iter_content(chunk_size=64*1024):
                if not part:continue
                byte_count+=len(part)
                if byte_count>max_bytes:raise GovHubSourceError('CSV export exceeds maximum size')
                if len(head)<512:head+=(part[:512-len(head)])
                digest.update(part);fh.write(part)
        if byte_count<25 or not head.strip():raise GovHubSourceError('CSV export empty or malformed')
        prefix=head.lstrip().lower()
        if prefix.startswith((b'<!doctype',b'<html',b'<?xml',b'{',b'[')) or b'<html' in prefix[:200] or head.startswith(b'PK\x03\x04'):
            raise GovHubSourceError('GovHub response is not CSV content')
        if b',' not in head and b'\t' not in head:
            raise GovHubSourceError('GovHub export has no recognizable delimited columns')
        temp.replace(dest)
        return digest.hexdigest()
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    finally:
        response.close()


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-date',required=True,type=date.fromisoformat)
    parser.add_argument('--output',type=Path,default=Path('/tmp/lee-govhub-report.csv'))
    parser.add_argument('--official-export-confirmed',action='store_true',
                        help='You verified this direct CSV was issued via the official GovHub report portal')
    parser.add_argument('--write',action='store_true',help='Insert only into private review tables; no lead release')
    args=parser.parse_args(argv)
    if args.report_date>date.today():parser.error('Report date cannot be in the future')
    if not args.official_export_confirmed:
        parser.error('You must confirm that the direct CSV link is an authorized GovHub export')
    url=os.getenv('LEE_GOVHUB_EXPORT_URL','')
    if not validate_govhub_export_url(url):
        print('BLOCKED: set LEE_GOVHUB_EXPORT_URL secret to an actual authorized GovHub CSV download link',file=sys.stderr)
        return 2
    try:
        with requests.Session() as session:
            digest=download_govhub_csv(session,url,args.output)
        # Importing here keeps standalone safety tests dependency-light.
        from lee_tax_collector_report_worker import (read_report,insert_report,staging_dsn_is_safe)
        verified_digest,records=read_report(args.output,args.report_date)
        if verified_digest!=digest:
            raise GovHubSourceError('CSV changed after verified download')
        print('Official-export format validation succeeded: rows=',len(records),'sha256=',digest)
        if not args.write:
            print('DRY RUN: no staging writes')
            return 0
        dsn=os.getenv('STAGING_SUPABASE_DB_URL','')
        if not staging_dsn_is_safe(dsn):
            raise GovHubSourceError('Approved staging-only database URL is required')
        import psycopg2
        conn=psycopg2.connect(dsn,connect_timeout=12,
                              sslmode='verify-full' if os.getenv('PGSSLROOTCERT') else 'require')
        try:
            _,matched=insert_report(conn,digest,args.report_date,records)
            print('Staging observations matched:',matched, 'status: PENDING_PROVENANCE_REVIEW')
            print('No ownership, valuation, tax delinquency, qualification or outreach promoted')
        except Exception:
            conn.rollback();raise
        finally:
            conn.close()
        return 0
    except (requests.RequestException, GovHubSourceError,ValueError,OSError) as exc:
        # Export links may contain signed tokens; do not print the URL or exception text.
        print('BLOCKED: official GovHub export not validated ('+type(exc).__name__+')',file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())