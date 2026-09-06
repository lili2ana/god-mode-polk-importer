#!/usr/bin/env python3
from ftplib import FTP_TLS
from pathlib import Path
import csv
import zipfile
import os

HOST='ftp.polkflpa.gov'
DIR='/AppraisalData'
FEEDS=('ftp_parceltax.zip','ftp_permit.zip')


def download(ftp, remote, local):
    with open(local,'wb') as f:
        ftp.retrbinary(f'RETR {remote}', f.write, blocksize=1024*1024)


def inspect_feed(zip_path: Path):
    compressed=zip_path.stat().st_size
    with zipfile.ZipFile(zip_path) as zf:
        members=[i for i in zf.infolist() if not i.is_dir()]
        if not members:
            raise RuntimeError(f'{zip_path.name}: empty archive')
        member=max(members, key=lambda i:i.file_size)
        extracted=member.file_size
        txt_path=zip_path.with_suffix('.txt')
        with zf.open(member) as src, open(txt_path,'wb') as dst:
            while True:
                chunk=src.read(1024*1024)
                if not chunk:
                    break
                dst.write(chunk)

    physical_lines=0
    with open(txt_path,'rb') as f:
        for _ in f:
            physical_lines += 1

    rows=0
    width_errors=0
    serialized_bytes=0
    header=None
    sample=[]
    with open(txt_path,'r',encoding='cp1252',newline='') as f:
        reader=csv.reader(f)
        header=next(reader)
        expected=len(header)
        for row in reader:
            rows += 1
            if len(row) != expected:
                width_errors += 1
            serialized_bytes += sum(len(v.encode('cp1252',errors='replace')) for v in row)
            if len(sample) < 3:
                sample.append(row)

    print(f'feed={zip_path.name}')
    print(f'compressed_bytes={compressed}')
    print(f'compressed_mb={compressed/1024/1024:.2f}')
    print(f'extracted_bytes={extracted}')
    print(f'extracted_mb={extracted/1024/1024:.2f}')
    print(f'physical_lines={physical_lines}')
    print(f'logical_rows={rows}')
    print(f'column_count={len(header)}')
    print('header=' + '|'.join(header))
    print(f'width_errors={width_errors}')
    print(f'approx_serialized_field_bytes={serialized_bytes}')
    print(f'approx_serialized_field_mb={serialized_bytes/1024/1024:.2f}')
    if rows:
        print(f'avg_serialized_field_bytes_per_row={serialized_bytes/rows:.2f}')
    for i,row in enumerate(sample,1):
        print(f'sample_{i}=' + '|'.join(row[:min(len(row),8)]))
    print('---')
    txt_path.unlink(missing_ok=True)


def main():
    ftp=FTP_TLS(HOST, timeout=180)
    ftp.login()
    ftp.prot_p()
    ftp.cwd(DIR)
    try:
        for feed in FEEDS:
            local=Path(feed)
            download(ftp, feed, local)
            inspect_feed(local)
            local.unlink(missing_ok=True)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass

if __name__=='__main__':
    main()
