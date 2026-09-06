#!/usr/bin/env python3
from ftplib import FTP_TLS

HOST='ftp.polkflpa.gov'
DIR='/AppraisalData'
KEYWORDS=('tax','permit','parcel','owner','legal','sale')

ftp=FTP_TLS(HOST, timeout=120)
ftp.login()
ftp.prot_p()
ftp.cwd(DIR)
print(f'cwd={ftp.pwd()}')

entries=[]
try:
    for name, facts in ftp.mlsd():
        if facts.get('type') != 'file':
            continue
        size = int(facts.get('size') or 0)
        entries.append((name,size))
except Exception:
    for name in ftp.nlst():
        try:
            size=ftp.size(name) or 0
        except Exception:
            size=0
        entries.append((name,int(size)))

for name,size in sorted(entries, key=lambda x:x[0].lower()):
    low=name.lower()
    if any(k in low for k in KEYWORDS):
        print(f'{name}\t{size}\t{size/1024/1024:.2f} MB')

print(f'total_files={len(entries)}')
ftp.quit()
