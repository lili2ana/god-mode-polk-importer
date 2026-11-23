#!/usr/bin/env python3
"""Import Lee Property Appraiser owner / just-value evidence without claiming verification.

Accept a normalized CSV exported from the LeePA reports, OR the official monthly
LeePA parcel-data TXT ZIP *if its actual content has unambiguous field headers*.
Native fixed-width or unidentified TXT layouts fail closed rather than guessing.
This only updates private REVIEW_REQUIRED evidence, preserving verified records.

Expected columns (case-insensitive aliases supported):
  STRAP, OWNER_NAME, SITEADDR, JUST_VALUE, TAX_YEAR

Official sources:
  https://www.leepa.org/Roll/TaxRoll.aspx
  https://www.leepa.org/TaxRoll/ParcelData/LCPA_Parcel_Data_TXT.zip
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal,InvalidOperation
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import urlsplit
import zipfile

import requests

PROJECT='ltwqgkcblvtscgldxvjw'
OFFICIAL_ZIP_URL='https://www.leepa.org/TaxRoll/ParcelData/LCPA_Parcel_Data_TXT.zip'
REPORT_URL='https://www.leepa.org/OnlineReports/Reports.aspx'

class AppraiserSourceError(ValueError):pass

ALIASES={
 'strap':{'strap','parcel_id','parcelid','parcel_number','parcelno','pr_strap'},
 'owner':{'owner_name','owner','o_name','owner1','owner_1'},
 'site':{'siteaddr','site_address','property_address','situs_address','situs','location_address'},
 'value':{'just_value','market_value','justvalue','just_val','appraiser_just_value'},
 'year':{'tax_year','valuation_year','year','roll_year'},
}

def validate_appraiser_source_url(url: str) -> bool:
    try:
        p=urlsplit(url)
        return (p.scheme=='https' and p.hostname in ('leepa.org','www.leepa.org')
                and p.port in (None,443) and not p.username and not p.password
                and not p.fragment and not p.query
                and p.path in ('/Roll/TaxRoll.aspx','/OnlineReports/Reports.aspx',
                               '/TaxRoll/ParcelData/LCPA_Parcel_Data_TXT.zip'))
    except (ValueError,TypeError):return False


def _normalize_name(name):
    return re.sub(r'[^a-z0-9]+','_',str(name).replace('\ufeff','').strip().lower()).strip('_')


def _column_mapping(fieldnames):
    names=[_normalize_name(f) for f in fieldnames]
    out={}
    for key,aliases in ALIASES.items():
        hits=[i for i,n in enumerate(names) if n in aliases]
        if len(hits)>1:raise AppraiserSourceError('Ambiguous '+key+' columns; explicit mapping required')
        if hits:out[key]=fieldnames[hits[0]]
        elif key!='year':raise AppraiserSourceError('Missing required source column: '+key)
    return out


def _normalize_strap(value):
    val=re.sub(r'[-.\s]','',str(value)).upper()
    if not re.fullmatch(r'[A-Z0-9]{17}',val):raise AppraiserSourceError('Invalid source STRAP')
    return val


def parse_valuation_csv(source, valuation_year:int, max_rows:int=1_500_000):
    if not (2020<=valuation_year<=date.today().year):
        raise AppraiserSourceError('Invalid valuation year')
    sample=source.read(4096)
    source.seek(0)
    if not sample or sample.lstrip().lower().startswith(('<html','<!doctype','{','[')):
        raise AppraiserSourceError('Source is not a delimited real-property report')
    try:delimiter=csv.Sniffer().sniff(sample,delimiters=',\t|').delimiter
    except csv.Error:delimiter=','
    reader=csv.DictReader(source,delimiter=delimiter)
    if not reader.fieldnames:raise AppraiserSourceError('Report lacks a header')
    mapping=_column_mapping(reader.fieldnames)
    out=[];seen=set()
    for i,row in enumerate(reader,start=2):
        if i>max_rows+1:raise AppraiserSourceError('Source row count exceeds safe limit')
        raw_site=(row[mapping['site']] or '').strip()
        if 'LEHIGH ACRES' not in raw_site.upper():continue
        strap=_normalize_strap(row[mapping['strap']])
        if strap in seen:raise AppraiserSourceError('Duplicate normalized STRAP in Lehigh report')
        seen.add(strap)
        owner=(row[mapping['owner']] or '').strip()
        if not owner or len(owner)>300 or len(raw_site)>500:
            raise AppraiserSourceError('Missing or overlong owner/site evidence')
        try:
            amount=Decimal(str(row[mapping['value']]).replace(',','').replace('$','').strip())
        except (InvalidOperation,TypeError,ValueError) as exc:
            raise AppraiserSourceError('Invalid official appraiser just value') from exc
        if not amount.is_finite() or amount<=0 or amount.as_tuple().exponent < -2:
            raise AppraiserSourceError('Nonpositive or ambiguous valuation')
        if 'year' in mapping:
            try:actual_year=int(row[mapping['year']])
            except (ValueError,TypeError) as exc:raise AppraiserSourceError('Invalid valuation year field') from exc
            if actual_year!=valuation_year:
                raise AppraiserSourceError('Source row valuation year mismatch')
        out.append({'normalized_strap':strap,'owner_name':owner,
                    'site_address':raw_site,'just_value':str(amount.quantize(Decimal('0.01'))),
                    'valuation_year':valuation_year})
    if not out:raise AppraiserSourceError('No source-backed Lehigh Acres records found')
    return out


def parse_official_zip(path:Path,valuation_year:int):
    try:archive=zipfile.ZipFile(path)
    except (zipfile.BadZipFile,OSError) as exc:raise AppraiserSourceError('Not an official parcel ZIP archive') from exc
    with archive:
        members=archive.infolist()
        if not 1<=len(members)<=40:raise AppraiserSourceError('Unexpected archive member count')
        for item in members:
            parts=PurePosixPath(item.filename.replace('\\','/')).parts
            if item.filename.startswith('/') or '..' in parts or item.flag_bits & 1 or item.file_size>400_000_000 or (item.compress_size and item.file_size/item.compress_size>200):
                raise AppraiserSourceError('Unsafe or unsupported archive member')
        candidates=[m for m in members if not m.is_dir() and m.filename.lower().endswith(('.txt','.csv'))]
        if len(candidates)!=1:
            # Multiple or undocumented native layouts require explicit normalization.
            raise AppraiserSourceError('Archive needs a documented one-file parcel CSV/TXT layout')
        member=candidates[0]
        with archive.open(member) as binary,io.TextIOWrapper(binary,encoding='utf-8-sig',newline='') as reader:
            return parse_valuation_csv(reader,valuation_year)


def download_official_zip(session,dest:Path,max_bytes:int=250_000_000)->str:
    response=session.get(OFFICIAL_ZIP_URL,stream=True,allow_redirects=True,timeout=(20,120))
    tmp=dest.with_suffix('.incomplete')
    try:
        response.raise_for_status()
        for hop in [*response.history,response]:
            if not validate_appraiser_source_url(hop.url):
                raise AppraiserSourceError('Official ZIP source redirected outside allowed URL')
        declared=response.headers.get('Content-Length')
        if declared and int(declared)>max_bytes:raise AppraiserSourceError('Parcel ZIP too large')
        dest.parent.mkdir(parents=True,exist_ok=True)
        n=0;digest=hashlib.sha256()
        with tmp.open('wb') as fh:
            for chunk in response.iter_content(chunk_size=128*1024):
                if not chunk:continue
                n+=len(chunk)
                if n>max_bytes:raise AppraiserSourceError('Parcel ZIP too large')
                digest.update(chunk);fh.write(chunk)
        if not zipfile.is_zipfile(tmp):raise AppraiserSourceError('Official source did not return ZIP data')
        tmp.replace(dest)
        return digest.hexdigest()
    except Exception:
        tmp.unlink(missing_ok=True);raise
    finally:response.close()


def stage_valuation(conn,records,source_url,report_date,source_sha256):
    """Persist *only* for review, never set VERIFIED_SOURCE or verified_at."""
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO god_mode_ops.lee_appraiser_import_batches
            (source_url,archive_sha256,report_date,valuation_year,row_count,validation_status)
            VALUES (%s,%s,%s,%s,%s,'PENDING_PROVENANCE_REVIEW')
            ON CONFLICT(archive_sha256) DO UPDATE SET row_count=EXCLUDED.row_count
            RETURNING batch_id''',
            (source_url,source_sha256,report_date,records[0]['valuation_year'],len(records)))
        batch_id=cur.fetchone()[0]
        values=[(r['normalized_strap'],r['owner_name'],r['site_address'],r['just_value'],r['valuation_year'],source_url,batch_id) for r in records]
        execute_values(cur,'''INSERT INTO god_mode_ops.lee_verified_owner_valuation
          (parcel_id,owner_name,current_site_address,appraiser_just_value_usd,
           valuation_year,source_authority,source_url,source_checked_at,verification_status,verified_at,import_batch_id)
          SELECT s.parcel_id,v.owner_name,v.site_address,v.just_value,v.valuation_year,
                 'LEE_PROPERTY_APPRAISER',v.source_url,now(),'REVIEW_REQUIRED',NULL,v.batch_id
          FROM (VALUES %s) AS v(strap,owner_name,site_address,just_value,valuation_year,source_url,batch_id)
          JOIN god_mode_ops.lee_parcel_qualification_screening s
            ON regexp_replace(upper(s.parcel_id),'[-.[:space:]]','','g')=v.strap
             AND s.county='Lee County'
          ON CONFLICT(parcel_id) DO UPDATE SET
             owner_name=EXCLUDED.owner_name,current_site_address=EXCLUDED.current_site_address,
             appraiser_just_value_usd=EXCLUDED.appraiser_just_value_usd,
             valuation_year=EXCLUDED.valuation_year,source_url=EXCLUDED.source_url,
             source_checked_at=now(),verification_status='REVIEW_REQUIRED',verified_at=NULL,
             import_batch_id=EXCLUDED.import_batch_id
          WHERE god_mode_ops.lee_verified_owner_valuation.verification_status<>'VERIFIED_SOURCE'
          ''', values, template="(%s,%s,%s,%s::numeric,%s,%s,%s::uuid)",page_size=500)
        cur.execute('SELECT count(*) FROM god_mode_ops.lee_verified_owner_valuation WHERE import_batch_id=%s',(batch_id,))
        matched=cur.fetchone()[0]
    conn.commit()
    return matched


def valid_dsn(dsn:str)->bool:
    try:
        p=urlsplit(dsn);host=p.hostname or '';user=p.username or ''
        return p.scheme in ('postgres','postgresql') and (
          (host==f'db.{PROJECT}.supabase.co' and user=='postgres') or
          (host.endswith('.pooler.supabase.com') and user==f'postgres.{PROJECT}'))
    except (ValueError,TypeError):return False


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv',type=Path,help='Authorized normalized CSV from LeePA Online Reports')
    source.add_argument('--download-monthly-zip',action='store_true')
    ap.add_argument('--report-date',type=date.fromisoformat,required=True)
    ap.add_argument('--valuation-year',type=int,required=True)
    ap.add_argument('--official-export-confirmed',action='store_true')
    ap.add_argument('--write',action='store_true')
    ap.add_argument('--download-dir',type=Path,default=Path('/tmp/lee-appraiser'))
    args=ap.parse_args(argv)
    if not args.official_export_confirmed:
        ap.error('Explicit official-source confirmation is required')
    if args.report_date>date.today() or args.valuation_year>date.today().year:
        ap.error('Future valuation/report date')
    if args.download_monthly_zip:
        args.download_dir.mkdir(parents=True,exist_ok=True)
        path=args.download_dir/'LCPA_Parcel_Data_TXT.zip'
        with requests.Session() as session:
            digest=download_official_zip(session,path)
        data=parse_official_zip(path,args.valuation_year)
        src=OFFICIAL_ZIP_URL
    else:
        if args.csv.stat().st_size>250_000_000:raise AppraiserSourceError('Source CSV too large')
        digest=hashlib.sha256(args.csv.read_bytes()).hexdigest()
        with args.csv.open(encoding='utf-8-sig',newline='') as fh:
            data=parse_valuation_csv(fh,args.valuation_year)
        src=REPORT_URL
    print('Parsed official format:',len(data),'Lehigh records. SHA256:',digest)
    if not args.write:
        print('DRY RUN: No database writes')
        return 0
    dsn=os.getenv('STAGING_SUPABASE_DB_URL','')
    if not valid_dsn(dsn):raise AppraiserSourceError('Invalid staging database URL')
    import psycopg2
    conn=psycopg2.connect(dsn,connect_timeout=12,sslmode='verify-full' if os.getenv('PGSSLROOTCERT') else 'require')
    try:
        matched=stage_valuation(conn,data,src,args.report_date,digest)
        print('Private staging owner/valuation observations:',matched, 'REVIEW_REQUIRED (NOT qualified)')
    except Exception:
        conn.rollback();raise
    finally:conn.close()
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (AppraiserSourceError,requests.RequestException,OSError) as exc:
        print('BLOCKED: Appraiser import failed closed ('+type(exc).__name__+')',file=sys.stderr)
        sys.exit(2)