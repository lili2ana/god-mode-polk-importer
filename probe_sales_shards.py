#!/usr/bin/env python3
import csv, io, zipfile, hashlib, statistics
from collections import Counter, defaultdict
from ftplib import FTP_TLS
from pathlib import Path

HOST='ftp.polkflpa.gov'
REMOTE='/AppraisalData/ftp_sales.zip'
OUT=Path('ftp_sales.zip')
PARTITION_COUNTS=(32,64,96,128)

ftp=FTP_TLS(HOST,timeout=120); ftp.login(); ftp.prot_p()
with OUT.open('wb') as f: ftp.retrbinary(f'RETR {REMOTE}', f.write, blocksize=1024*1024)
ftp.quit()

with zipfile.ZipFile(OUT) as zf:
    member=[i for i in zf.infolist() if not i.is_dir()][0]
    with zf.open(member) as raw:
        text=io.TextIOWrapper(raw,encoding='cp1252',newline='')
        r=csv.reader(text)
        header=next(r)
        rows=0
        serialized_bytes=0
        per_n={n:Counter() for n in PARTITION_COUNTS}
        row_lengths=[]
        ln_gap_parcels=defaultdict(list)
        for row in r:
            if len(row)!=15: raise SystemExit(f'width drift {len(row)}')
            rows+=1
            pid=row[0].strip(); ln=row[2].strip()
            if not pid or not ln: raise SystemExit('blank key')
            b=sum(len(v.encode('utf-8')) for v in row)+14
            serialized_bytes += b
            if len(row_lengths)<200000:
                row_lengths.append(b)
            h=int(hashlib.sha256(pid.encode()).hexdigest()[:16],16)
            for n in PARTITION_COUNTS: per_n[n][h % n]+=1
            ln_gap_parcels[pid].append(int(ln))

print('rows=',rows)
print('approx_serialized_field_bytes=',serialized_bytes)
print('approx_serialized_field_mb=',round(serialized_bytes/1024/1024,2))
if row_lengths:
    s=sorted(row_lengths)
    print('sample_avg_row_payload_bytes=',round(statistics.mean(s),2))
    print('sample_p95_row_payload_bytes=',s[int(len(s)*0.95)-1])
    print('sample_p99_row_payload_bytes=',s[int(len(s)*0.99)-1])
    print('sample_max_row_payload_bytes=',max(s))
for n,c in per_n.items():
    vals=list(c.values())
    print(f'shards_{n}: min={min(vals)} max={max(vals)} avg={round(sum(vals)/n,2)} max_to_avg={round(max(vals)/(sum(vals)/n),3)}')

multi=0; contiguous=0; gapped=0; out_of_order_like=0
examples=[]
for pid, nums in ln_gap_parcels.items():
    if len(nums)>1:
        multi+=1
        u=sorted(set(nums))
        if len(u)!=len(nums):
            out_of_order_like+=1
        expected=list(range(min(u), max(u)+1))
        if u==expected:
            contiguous+=1
        else:
            gapped+=1
            if len(examples)<10:
                missing=[x for x in expected if x not in set(u)][:20]
                examples.append((pid,u[:30],missing))
print('multi_sale_parcels=',multi)
print('contiguous_ln_num_parcels=',contiguous)
print('gapped_ln_num_parcels=',gapped)
print('duplicate_ln_num_within_parcel=',out_of_order_like)
for e in examples: print('gap_example=',e)
