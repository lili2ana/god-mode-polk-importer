#!/usr/bin/env python3
import csv, io, os, zipfile
from collections import Counter, defaultdict
from datetime import datetime
from ftplib import FTP_TLS
from pathlib import Path
import psycopg2

HOST='ftp.polkflpa.gov'
REMOTE='/AppraisalData/ftp_sales.zip'
OUT=Path('ftp_sales.zip')


def parse_year(s):
    s=(s or '').strip()
    if not s: return None
    for fmt in ('%m/%d/%Y','%Y-%m-%d','%m/%d/%y','%Y%m%d'):
        try: return datetime.strptime(s,fmt).year
        except ValueError: pass
    # fallback for strings beginning/ending with a 4-digit year
    for token in s.replace('-','/').split('/'):
        if token.isdigit() and len(token)==4:
            y=int(token)
            if 1900 <= y <= 2100: return y
    return None


def main():
    db=os.environ['SUPABASE_DB_URL']
    conn=psycopg2.connect(db)
    with conn, conn.cursor() as cur:
        cur.execute("select parcel_id, coalesce(dordesc1,'') from public.polk_parcel_v2 where dordesc='RES'")
        res={pid:desc for pid,desc in cur.fetchall()}
    conn.close()

    print(f'residential_parcels={len(res)}')
    vacant={pid for pid,desc in res.items() if desc.lower().startswith('vac')}
    improved=set(res)-vacant
    print(f'residential_vacant_parcels={len(vacant)}')
    print(f'residential_improved_parcels={len(improved)}')

    ftp=FTP_TLS(HOST,timeout=180); ftp.login(); ftp.prot_p()
    with OUT.open('wb') as f: ftp.retrbinary(f'RETR {REMOTE}',f.write,blocksize=1024*1024)
    ftp.quit()

    by_year=Counter(); parcels_by_year=defaultdict(set); payload_by_year=Counter()
    by_year_improved=Counter(); by_year_vacant=Counter()
    unparsed_dates=0; total_res_rows=0; total_res_payload=0
    with zipfile.ZipFile(OUT) as zf:
        member=[i for i in zf.infolist() if not i.is_dir()][0]
        with zf.open(member) as raw:
            text=io.TextIOWrapper(raw,encoding='cp1252',newline='')
            r=csv.reader(text); header=next(r); h={name:i for i,name in enumerate(header)}
            for row in r:
                if len(row)!=len(header): raise SystemExit(f'width drift {len(row)}')
                pid=row[h['PARCEL_ID']].strip()
                if pid not in res: continue
                total_res_rows+=1
                b=sum(len(v.encode('utf-8')) for v in row)+(len(row)-1)
                total_res_payload+=b
                y=parse_year(row[h['SALEDT']])
                if y is None:
                    unparsed_dates+=1; continue
                by_year[y]+=1; parcels_by_year[y].add(pid); payload_by_year[y]+=b
                if pid in vacant: by_year_vacant[y]+=1
                else: by_year_improved[y]+=1

    print(f'residential_sales_rows_all_history={total_res_rows}')
    print(f'residential_sales_payload_mb_all_history={total_res_payload/1024/1024:.2f}')
    print(f'unparsed_sale_dates={unparsed_dates}')
    for y in range(2015,2027):
        print(f'year={y} rows={by_year[y]} parcels={len(parcels_by_year[y])} improved_rows={by_year_improved[y]} vacant_rows={by_year_vacant[y]} payload_mb={payload_by_year[y]/1024/1024:.2f}')

    for start in (2019,2020,2022,2024):
        rows=sum(c for y,c in by_year.items() if y>=start)
        payload=sum(c for y,c in payload_by_year.items() if y>=start)
        parcel_union=set()
        for y,s in parcels_by_year.items():
            if y>=start: parcel_union.update(s)
        print(f'window_start={start}-01-01 rows={rows} unique_parcels={len(parcel_union)} payload_mb={payload/1024/1024:.2f}')

if __name__=='__main__': main()
