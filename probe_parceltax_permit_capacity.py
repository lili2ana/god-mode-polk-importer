#!/usr/bin/env python3
from ftplib import FTP_TLS
from pathlib import Path
import csv
import zipfile
from collections import Counter, defaultdict

HOST='ftp.polkflpa.gov'
DIR='/AppraisalData'
FEEDS=('ftp_parceltax.zip','ftp_permit.zip')


def download(ftp, remote, local):
    with open(local,'wb') as f:
        ftp.retrbinary(f'RETR {remote}', f.write, blocksize=1024*1024)


def to_num(s):
    s=(s or '').strip().replace(',','').replace('$','')
    if not s:
        return 0.0
    try: return float(s)
    except Exception: return 0.0


def inspect_feed(zip_path: Path):
    compressed=zip_path.stat().st_size
    with zipfile.ZipFile(zip_path) as zf:
        members=[i for i in zf.infolist() if not i.is_dir()]
        if not members: raise RuntimeError(f'{zip_path.name}: empty archive')
        member=max(members, key=lambda i:i.file_size)
        extracted=member.file_size
        txt_path=zip_path.with_suffix('.txt')
        with zf.open(member) as src, open(txt_path,'wb') as dst:
            while chunk := src.read(1024*1024): dst.write(chunk)

    with open(txt_path,'rb') as f:
        physical_lines=sum(1 for _ in f)

    rows=width_errors=serialized_bytes=0
    header=None; sample=[]
    parcel_rows=Counter(); parcel_lnnums=defaultdict(set)
    positive_due_rows=0; parcels_with_positive_due=set(); total_due=0.0
    with open(txt_path,'r',encoding='latin-1',newline='') as f:
        reader=csv.reader(f)
        header=next(reader); expected=len(header)
        h={name:i for i,name in enumerate(header)}
        for row in reader:
            rows += 1
            if len(row) != expected:
                width_errors += 1
            serialized_bytes += sum(len(v.encode('latin-1')) for v in row)
            if len(sample) < 3: sample.append(row)
            if zip_path.name == 'ftp_parceltax.zip' and len(row) == expected:
                pid=row[h['PARCEL_ID']].strip(); ln=row[h['LNNUM']].strip()
                if pid:
                    parcel_rows[pid]+=1
                    if ln: parcel_lnnums[pid].add(ln)
                due=to_num(row[h['TAXESDUE']])
                if due > 0:
                    positive_due_rows += 1
                    total_due += due
                    if pid: parcels_with_positive_due.add(pid)

    print(f'feed={zip_path.name}')
    print(f'compressed_mb={compressed/1024/1024:.2f}')
    print(f'extracted_mb={extracted/1024/1024:.2f}')
    print(f'physical_lines={physical_lines}')
    print(f'logical_rows={rows}')
    print(f'column_count={len(header)}')
    print('header=' + '|'.join(header))
    print(f'width_errors={width_errors}')
    print(f'approx_serialized_field_mb={serialized_bytes/1024/1024:.2f}')
    if rows: print(f'avg_serialized_field_bytes_per_row={serialized_bytes/rows:.2f}')
    if zip_path.name == 'ftp_parceltax.zip':
        unique=len(parcel_rows)
        counts=list(parcel_rows.values())
        duplicate_ln_parcels=sum(1 for pid,c in parcel_rows.items() if len(parcel_lnnums[pid]) != c)
        print(f'unique_parcels={unique}')
        print(f'avg_rows_per_parcel={sum(counts)/unique:.2f}' if unique else 'avg_rows_per_parcel=0')
        print(f'min_rows_per_parcel={min(counts) if counts else 0}')
        print(f'max_rows_per_parcel={max(counts) if counts else 0}')
        print(f'parcels_with_duplicate_lnnum={duplicate_ln_parcels}')
        print(f'positive_taxesdue_rows={positive_due_rows}')
        print(f'parcels_with_any_positive_taxesdue={len(parcels_with_positive_due)}')
        print(f'sum_positive_taxesdue={total_due:.2f}')
        print('note=no_year_status_certificate_or_delinquency_field_in_source_header')
    for i,row in enumerate(sample,1):
        print(f'sample_{i}=' + '|'.join(row[:min(len(row),8)]))
    print('---')
    txt_path.unlink(missing_ok=True)


def main():
    ftp=FTP_TLS(HOST, timeout=180); ftp.login(); ftp.prot_p(); ftp.cwd(DIR)
    try:
        for feed in FEEDS:
            local=Path(feed); download(ftp,feed,local); inspect_feed(local); local.unlink(missing_ok=True)
    finally:
        try: ftp.quit()
        except Exception: pass

if __name__=='__main__': main()
