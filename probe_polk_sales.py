#!/usr/bin/env python3
from ftplib import FTP_TLS
from pathlib import Path
import csv, io, zipfile

HOST = 'ftp.polkflpa.gov'
REMOTE = '/AppraisalData/ftp_sales.zip'
OUT = Path('ftp_sales.zip')


def main():
    ftp = FTP_TLS(HOST, timeout=180)
    ftp.login()
    ftp.prot_p()
    size = ftp.size(REMOTE)
    print(f'FTPS host={HOST}')
    print(f'remote={REMOTE}')
    print(f'remote_size={size}')
    with OUT.open('wb') as f:
        ftp.retrbinary(f'RETR {REMOTE}', f.write, blocksize=1024*1024)
    ftp.quit()

    print(f'downloaded_size={OUT.stat().st_size}')
    print(f'zip_valid={zipfile.is_zipfile(OUT)}')
    if not zipfile.is_zipfile(OUT):
        raise SystemExit('sales feed is not a valid ZIP')

    with zipfile.ZipFile(OUT) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if not members:
            raise SystemExit('sales ZIP contains no data file')
        member = members[0]
        print(f'zip_member={member.filename}')
        print(f'uncompressed_size={member.file_size}')

        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding='cp1252', errors='replace', newline='')
            first = text.readline()
            delimiter = '\t' if '\t' in first and first.count('\t') > first.count(',') else ','
            header = next(csv.reader([first], delimiter=delimiter))
            header = [h.strip().strip('"') for h in header]
            print(f'delimiter={repr(delimiter)}')
            print(f'column_count={len(header)}')
            print(f'header_fields={header}')

            reader = csv.reader(text, delimiter=delimiter)
            row_count = 0
            bad_width = 0
            blank_parcel = 0
            samples = []
            candidate_indexes = {name: header.index(name) for name in header if name in {
                'PARCEL_ID','LN_NUM','SALE_NUM','OR_BOOK','OR_PAGE','SALE_DATE','SALE_PRICE','QUAL_CD','QUALIFICATION_CODE'
            }}
            print(f'candidate_key_columns={candidate_indexes}')

            unique_counts = {k: set() for k in candidate_indexes}
            pair_specs = []
            if 'PARCEL_ID' in candidate_indexes:
                for other in ('LN_NUM','SALE_NUM','OR_BOOK','OR_PAGE'):
                    if other in candidate_indexes:
                        pair_specs.append(('PARCEL_ID', other))
            pair_seen = {p: set() for p in pair_specs}
            pair_dups = {p: 0 for p in pair_specs}

            for row in reader:
                if not row:
                    continue
                row_count += 1
                if len(row) != len(header):
                    bad_width += 1
                    if bad_width <= 3:
                        print(f'bad_width_sample_{bad_width}=fields:{len(row)} row:{row[:12]}')
                    continue
                if len(samples) < 3:
                    samples.append(row)
                if 'PARCEL_ID' in candidate_indexes:
                    pid = row[candidate_indexes['PARCEL_ID']].strip()
                    if not pid:
                        blank_parcel += 1
                for name, idx in candidate_indexes.items():
                    value = row[idx].strip()
                    if value:
                        unique_counts[name].add(value)
                for pair in pair_specs:
                    a = row[candidate_indexes[pair[0]]].strip()
                    b = row[candidate_indexes[pair[1]]].strip()
                    key = (a,b)
                    if key in pair_seen[pair]:
                        pair_dups[pair] += 1
                    else:
                        pair_seen[pair].add(key)

    print(f'csv_data_rows={row_count}')
    print(f'bad_width_rows={bad_width}')
    print(f'blank_parcel_id_rows={blank_parcel}')
    for name, values in unique_counts.items():
        print(f'unique_{name.lower()}={len(values)}')
    for pair in pair_specs:
        print(f'duplicate_{pair[0].lower()}_{pair[1].lower()}_rows={pair_dups[pair]}')
    for i, row in enumerate(samples, 1):
        print(f'sample_row_{i}={row}')

    if 'PARCEL_ID' not in header:
        raise SystemExit('schema failure: PARCEL_ID not present')
    if bad_width:
        raise SystemExit(f'schema drift: {bad_width} rows do not match header width')
    if blank_parcel:
        raise SystemExit(f'key failure: {blank_parcel} rows have blank PARCEL_ID')

if __name__ == '__main__':
    main()
