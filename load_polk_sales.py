#!/usr/bin/env python3
import csv, hashlib, io, os, shutil, tempfile, zipfile
from ftplib import FTP_TLS
from pathlib import Path
import psycopg2

HOST='ftp.polkflpa.gov'
REMOTE='/AppraisalData/ftp_sales.zip'
EXPECTED_HEADER=['PARCEL_ID','SALE_ID','LN_NUM','SALEDT','PRICE','BOOK','PAGE','SALETYPE','TRNS_CD','TRNS_DSCR','INSTRTYP','INSTRTYP_DSCR','GRANTOR','GRANTEE','FORECLOSURE']
EXPECTED_ROWS=3031366
PARTITIONS=int(os.environ.get('SALES_PARTITIONS','32'))


def shard_for(parcel_id):
    return int(hashlib.sha256(parcel_id.encode('utf-8')).hexdigest()[:16],16) % PARTITIONS


def download_zip(path):
    ftp=FTP_TLS(HOST, timeout=180)
    ftp.login(); ftp.prot_p()
    size=ftp.size(REMOTE)
    print(f'remote_size={size}')
    with path.open('wb') as f:
        ftp.retrbinary(f'RETR {REMOTE}', f.write, blocksize=1024*1024)
    ftp.quit()
    print(f'downloaded_size={path.stat().st_size}')


def partition(zip_path, workdir):
    shard_paths=[workdir/f'sales_{i:02d}.csv' for i in range(PARTITIONS)]
    files=[p.open('w', newline='', encoding='utf-8') for p in shard_paths]
    writers=[csv.writer(f) for f in files]
    rows=0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members=[m for m in zf.infolist() if not m.is_dir()]
            if len(members)!=1: raise RuntimeError(f'expected one data member, got {len(members)}')
            print(f'zip_member={members[0].filename}')
            print(f'uncompressed_size={members[0].file_size}')
            with zf.open(members[0]) as raw:
                text=io.TextIOWrapper(raw, encoding='utf-8', errors='strict', newline='')
                reader=csv.reader(text)
                header=next(reader)
                if header != EXPECTED_HEADER: raise RuntimeError(f'header drift: {header}')
                for row in reader:
                    rows += 1
                    if len(row)!=15: raise RuntimeError(f'row {rows} width={len(row)}')
                    if not row[0].strip(): raise RuntimeError(f'row {rows} blank parcel_id')
                    writers[shard_for(row[0].strip())].writerow(row)
    finally:
        for f in files: f.close()
    print(f'source_rows={rows}')
    if rows != EXPECTED_ROWS: raise RuntimeError(f'row-count drift: expected {EXPECTED_ROWS}, got {rows}')
    return shard_paths, rows


def main():
    db=os.environ['SUPABASE_DB_URL']
    tmp=Path(tempfile.mkdtemp(prefix='polk_sales_'))
    try:
        z=tmp/'ftp_sales.zip'
        download_zip(z)
        shards, source_rows=partition(z,tmp)
        conn=psycopg2.connect(db)
        try:
            conn.autocommit=False
            with conn.cursor() as cur:
                cur.execute('select current_setting(\'transaction_read_only\'), pg_is_in_recovery()')
                print('db_preflight=',cur.fetchone())
                cur.execute('truncate table public.polk_sales_stage_v2')
            conn.commit()

            copy_sql='''COPY public.polk_sales_stage_v2
              (parcel_id,sale_id,ln_num,saledt,price,book,page,saletype,trns_cd,trns_dscr,instrtyp,instrtyp_dscr,grantor,grantee,foreclosure)
              FROM STDIN WITH (FORMAT csv)'''
            loaded_shards=0
            for p in shards:
                if p.stat().st_size==0: continue
                with conn.cursor() as cur, p.open('r',encoding='utf-8') as f:
                    cur.copy_expert(copy_sql,f)
                conn.commit(); loaded_shards += 1
                print(f'copied={p.name}')
            print(f'loaded_shards={loaded_shards}')

            with conn.cursor() as cur:
                cur.execute('select count(*) from public.polk_sales_stage_v2')
                stage_rows=cur.fetchone()[0]
                cur.execute("select count(*) from public.polk_sales_stage_v2 where parcel_id is null or btrim(parcel_id)='' or ln_num is null or btrim(ln_num)='' or ln_num !~ '^[0-9]+$'")
                bad_key=cur.fetchone()[0]
                cur.execute("select count(*) from public.polk_sales_stage_v2 where price is not null and btrim(price)<>'' and price !~ '^-?[0-9]+(\\.[0-9]+)?$'")
                bad_price=cur.fetchone()[0]
                cur.execute("select count(*) from public.polk_sales_stage_v2 where saledt is not null and btrim(saledt)<>'' and saledt !~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'")
                bad_date=cur.fetchone()[0]
                cur.execute('select count(*) from (select parcel_id,ln_num from public.polk_sales_stage_v2 group by 1,2 having count(*)>1) d')
                dup_keys=cur.fetchone()[0]
            print(f'stage_rows={stage_rows} bad_key={bad_key} bad_price={bad_price} bad_date={bad_date} dup_keys={dup_keys}')
            if stage_rows != source_rows or any((bad_key,bad_price,bad_date,dup_keys)):
                raise RuntimeError('sales stage verification failed; production untouched')

            with conn.cursor() as cur:
                cur.execute('select distinct left(parcel_id,3) from public.polk_sales_stage_v2 order by 1')
                prefixes=[r[0] for r in cur.fetchall() if r[0]]
            total=0
            merge_sql='''
              insert into public.polk_sales_v2
                (parcel_id,sale_id,ln_num,saledt,price,book,page,saletype,trns_cd,trns_dscr,instrtyp,instrtyp_dscr,grantor,grantee,foreclosure,updated_at)
              select parcel_id, nullif(sale_id,''), ln_num::integer,
                     case when nullif(btrim(saledt),'') is null then null else to_date(saledt,'MM/DD/YYYY') end,
                     nullif(btrim(price),'')::numeric,
                     nullif(book,''),nullif(page,''),nullif(saletype,''),nullif(trns_cd,''),nullif(trns_dscr,''),
                     nullif(instrtyp,''),nullif(instrtyp_dscr,''),nullif(grantor,''),nullif(grantee,''),nullif(foreclosure,''),now()
              from public.polk_sales_stage_v2
              where parcel_id >= %s and parcel_id < %s
              on conflict (parcel_id,ln_num) do update set
                sale_id=excluded.sale_id,saledt=excluded.saledt,price=excluded.price,book=excluded.book,page=excluded.page,
                saletype=excluded.saletype,trns_cd=excluded.trns_cd,trns_dscr=excluded.trns_dscr,instrtyp=excluded.instrtyp,
                instrtyp_dscr=excluded.instrtyp_dscr,grantor=excluded.grantor,grantee=excluded.grantee,
                foreclosure=excluded.foreclosure,updated_at=now()'''
            for prefix in prefixes:
                upper=str(int(prefix)+1).zfill(len(prefix))
                with conn.cursor() as cur:
                    cur.execute(merge_sql,(prefix,upper)); affected=cur.rowcount
                conn.commit(); total += affected
                print(f'merged_prefix={prefix} affected={affected}')

            with conn.cursor() as cur:
                cur.execute('select count(*) from public.polk_sales_v2')
                prod_rows=cur.fetchone()[0]
            print(f'prod_rows={prod_rows} total_upserted={total}')
            if prod_rows != source_rows:
                raise RuntimeError(f'production count mismatch stage={source_rows} prod={prod_rows}')

            with conn.cursor() as cur:
                cur.execute('''insert into public.god_mode_feed_status
                  (feed_name,source_file,source_size_bytes,source_row_count,stage_row_count,prod_row_count,column_count,mapping_version,status,verified_at,github_run_id,commit_sha,notes,updated_at)
                  values ('sales','ftp_sales.zip',%s,%s,%s,%s,15,'sales_v2_15cols_parcel_ln_key','verified',now(),%s,%s,%s,now())
                  on conflict (feed_name) do update set
                    source_file=excluded.source_file,source_size_bytes=excluded.source_size_bytes,source_row_count=excluded.source_row_count,
                    stage_row_count=excluded.stage_row_count,prod_row_count=excluded.prod_row_count,column_count=excluded.column_count,
                    mapping_version=excluded.mapping_version,status=excluded.status,verified_at=excluded.verified_at,
                    github_run_id=excluded.github_run_id,commit_sha=excluded.commit_sha,notes=excluded.notes,updated_at=excluded.updated_at''',
                    (z.stat().st_size,source_rows,source_rows,prod_rows,os.getenv('GITHUB_RUN_ID'),os.getenv('GITHUB_SHA'),'Official Polk PA AppraisalData ftp_sales.zip; full-file probe and stage/prod reconciliation passed.'))
            conn.commit()
            print('feed_status=verified')
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
