#!/usr/bin/env python3
import csv, io, zipfile
from ftplib import FTP_TLS
from pathlib import Path

HOST='ftp.polkflpa.gov'
REMOTE='/AppraisalData/ftp_parceltax.zip'
OUT=Path('ftp_parceltax.zip')

ftp=FTP_TLS(HOST, timeout=180)
ftp.login(); ftp.prot_p()
size=ftp.size(REMOTE)
print(f'FTPS host={HOST}')
print(f'remote={REMOTE}')
print(f'remote_size={size}')
with OUT.open('wb') as f:
    ftp.retrbinary(f'RETR {REMOTE}', f.write, blocksize=1024*1024)
ftp.quit()
print(f'downloaded_size={OUT.stat().st_size}')
print(f'zip_valid={zipfile.is_zipfile(OUT)}')
if not zipfile.is_zipfile(OUT):
    raise SystemExit('parcel-tax feed is not a valid ZIP')

with zipfile.ZipFile(OUT) as zf:
    members=[m for m in zf.infolist() if not m.is_dir()]
    if not members:
        raise SystemExit('ZIP contains no data file')
    member=members[0]
    print(f'zip_member={member.filename}')
    print(f'uncompressed_size={member.file_size}')
    with zf.open(member) as raw:
        text=io.TextIOWrapper(raw, encoding='utf-8', errors='replace', newline='')
        first=text.readline()
        delimiter='\t' if '\t' in first and first.count('\t') > first.count(',') else ','
        print(f'delimiter={delimiter!r}')
        text.seek(0)
        reader=csv.reader(text, delimiter=delimiter)
        header=next(reader)
        print(f'column_count={len(header)}')
        print(f'header_fields={header}')
        upper=[h.strip().upper() for h in header]
        parcel_idx=upper.index('PARCEL_ID') if 'PARCEL_ID' in upper else None
        key_candidates=[]
        for name in ('LN_NUM','NUM','TAX_YEAR','YEAR','YR','DISTRICT','TAXDIST','CURTAXDIST'):
            if name in upper:
                key_candidates.append((name,upper.index(name)))
        print(f'candidate_key_columns={dict(key_candidates)}')
        rows=0; bad_width=0; blank_parcel=0; samples=[]
        unique_parcels=set(); candidate_seen={name:set() for name,_ in key_candidates}; candidate_dups={name:0 for name,_ in key_candidates}
        pair_seen={}; pair_dups={}
        for name,idx in key_candidates:
            pair_seen[name]=set(); pair_dups[name]=0
        for row in reader:
            rows += 1
            if len(row)!=len(header):
                bad_width += 1
                continue
            if len(samples)<3: samples.append(row)
            pid=row[parcel_idx].strip() if parcel_idx is not None else ''
            if parcel_idx is not None:
                if not pid: blank_parcel += 1
                else: unique_parcels.add(pid)
            for name,idx in key_candidates:
                val=row[idx].strip()
                if val in candidate_seen[name]: candidate_dups[name]+=1
                else: candidate_seen[name].add(val)
                if parcel_idx is not None:
                    key=(pid,val)
                    if key in pair_seen[name]: pair_dups[name]+=1
                    else: pair_seen[name].add(key)
        print(f'csv_data_rows={rows}')
        print(f'bad_width_rows={bad_width}')
        print(f'blank_parcel_id_rows={blank_parcel}')
        print(f'unique_parcel_id={len(unique_parcels)}')
        for name,_ in key_candidates:
            print(f'unique_{name.lower()}={len(candidate_seen[name])}')
            print(f'duplicate_parcel_id_{name.lower()}_rows={pair_dups[name]}')
        for i,row in enumerate(samples,1):
            print(f'sample_row_{i}={row}')

if bad_width:
    raise SystemExit(f'schema drift: {bad_width} rows do not match header width')
if parcel_idx is not None and blank_parcel:
    raise SystemExit(f'key failure: {blank_parcel} rows have blank PARCEL_ID')
