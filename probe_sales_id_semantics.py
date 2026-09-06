#!/usr/bin/env python3
import csv, io, zipfile
from collections import Counter, defaultdict
from ftplib import FTP_TLS
from pathlib import Path

HOST='ftp.polkflpa.gov'
REMOTE='/AppraisalData/ftp_sales.zip'
OUT=Path('ftp_sales.zip')

ftp=FTP_TLS(HOST, timeout=120); ftp.login(); ftp.prot_p()
with OUT.open('wb') as f: ftp.retrbinary(f'RETR {REMOTE}', f.write, blocksize=1024*1024)
ftp.quit()

with zipfile.ZipFile(OUT) as zf:
    member=[i for i in zf.infolist() if not i.is_dir()][0]
    text=io.TextIOWrapper(zf.open(member), encoding='cp1252', newline='')
    r=csv.reader(text)
    header=[c.strip().strip('"').lstrip('\ufeff') for c in next(r)]
    ix={n:i for i,n in enumerate(header)}
    required=['PARCEL_ID','SALE_ID','LN_NUM','SALEDT']
    for k in required:
        if k not in ix: raise SystemExit(f'missing {k}')

    total=equal=neq=0
    sid_vals=Counter(); ln_vals=Counter(); sid_dates=defaultdict(set)
    parcel_rows=defaultdict(list)
    sid_parcels=defaultdict(set)
    sid_date_minmax={}
    for row in r:
        if len(row)!=len(header): raise SystemExit(f'width drift {len(row)}')
        total+=1
        pid=row[ix['PARCEL_ID']].strip(); sid=row[ix['SALE_ID']].strip(); ln=row[ix['LN_NUM']].strip(); dt=row[ix['SALEDT']].strip()
        equal += sid==ln
        neq += sid!=ln
        sid_vals[sid]+=1; ln_vals[ln]+=1; sid_dates[sid].add(dt); sid_parcels[sid].add(pid)
        if len(parcel_rows[pid]) < 25: parcel_rows[pid].append((ln,sid,dt))

    def numeric_order(vals):
        try: return sorted(vals, key=lambda x:int(x))
        except: return sorted(vals)

    contiguous=0; noncontig=0; parcels_multi=0; samples=[]
    for pid, vals in parcel_rows.items():
        if len(vals)<2: continue
        parcels_multi+=1
        lns=[v[0] for v in vals]
        try:
            nums=sorted(int(x) for x in lns)
            is_contig=(nums==list(range(min(nums), max(nums)+1)))
        except:
            is_contig=False
        contiguous += is_contig
        noncontig += (not is_contig)
        if len(samples)<8: samples.append((pid, vals[:12], is_contig))

    parcel_counts=Counter(len(v) for v in sid_parcels.values())
    date_card=Counter(len(v) for v in sid_dates.values())

print(f'total_rows={total}')
print(f'sale_id_equals_ln_num_rows={equal}')
print(f'sale_id_differs_ln_num_rows={neq}')
print(f'equality_pct={equal/total:.6%}')
print(f'unique_sale_id={len(sid_vals)}')
print(f'unique_ln_num={len(ln_vals)}')
print(f'sale_id_minmax=({min(sid_vals, key=lambda x:int(x))},{max(sid_vals, key=lambda x:int(x))})')
print(f'ln_num_minmax=({min(ln_vals, key=lambda x:int(x))},{max(ln_vals, key=lambda x:int(x))})')
print(f'sale_id_top_counts={sid_vals.most_common(10)}')
print(f'ln_num_top_counts={ln_vals.most_common(10)}')
print(f'sale_id_parcel_count_distribution={dict(sorted(parcel_counts.items())[:20])}')
print(f'max_parcels_per_sale_id={max(len(v) for v in sid_parcels.values())}')
print(f'sale_id_distinct_date_count_distribution={dict(sorted(date_card.items())[:20])}')
print(f'max_distinct_dates_per_sale_id={max(len(v) for v in sid_dates.values())}')
print(f'parcels_with_multiple_sales_sampled={parcels_multi}')
print(f'ln_num_contiguous_sampled_parcels={contiguous}')
print(f'ln_num_noncontiguous_sampled_parcels={noncontig}')
for s in samples: print(f'parcel_sequence_sample={s!r}')
