#!/usr/bin/env python3
import csv
import io
import os
import zipfile
from collections import Counter
from ftplib import FTP_TLS
from pathlib import Path

import psycopg2

HOST = "ftp.polkflpa.gov"
REMOTE = "/AppraisalData/ftp_sales.zip"
OUT = Path("ftp_sales.zip")


def normalize_header(row):
    return [c.strip().strip('"').lstrip("\ufeff") for c in row]


def main():
    ftp = FTP_TLS(HOST, timeout=120)
    ftp.login()
    ftp.prot_p()
    size = ftp.size(REMOTE)
    print(f"FTPS host={HOST}")
    print(f"remote={REMOTE}")
    print(f"remote_size={size}")
    with OUT.open("wb") as f:
        ftp.retrbinary(f"RETR {REMOTE}", f.write, blocksize=1024 * 1024)
    ftp.quit()

    print(f"downloaded_size={OUT.stat().st_size}")
    print(f"zip_valid={zipfile.is_zipfile(OUT)}")
    if not zipfile.is_zipfile(OUT):
        raise SystemExit("sales feed is not a valid ZIP")

    with zipfile.ZipFile(OUT) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if not members:
            raise SystemExit("sales ZIP contains no data file")
        member = members[0]
        print(f"zip_member={member.filename}")
        print(f"uncompressed_size={member.file_size}")
        raw_bytes = zf.read(member)

    # Polk CAMA text has already shown CP1252 characters in legal. Decode losslessly
    # with CP1252 for the probe; do not normalize semantic values.
    text = raw_bytes.decode("cp1252")
    physical_lines = text.splitlines()
    if not physical_lines:
        raise SystemExit("sales source is empty")

    header_line = physical_lines[0]
    header = normalize_header(next(csv.reader([header_line])))
    width = len(header)
    print(f"column_count={width}")
    print(f"header_fields={header}")
    print(f"physical_data_lines={len(physical_lines) - 1}")

    # Naive logical CSV parse: intentionally mirrors the failure mode that
    # under-counted legal, so we can detect quote/newline drift proactively.
    naive_reader = csv.reader(io.StringIO(text), delimiter=",")
    naive_header = normalize_header(next(naive_reader))
    naive_rows = 0
    naive_bad_width = 0
    naive_widths = Counter()
    naive_keys = set()
    naive_parcels = set()
    naive_blank_parcel = 0

    # Candidate key columns are discovered from the real header rather than assumed.
    upper = [h.upper() for h in header]
    parcel_idx = upper.index("PARCEL_ID") if "PARCEL_ID" in upper else None
    candidate_names = [
        "SALE_DATE", "SALE_DT", "DATE_SOLD", "SALEPRICE", "SALE_PRICE",
        "DEED_TYPE", "DEED_CD", "BOOK", "PAGE", "OR_BOOK", "OR_PAGE",
        "INSTRUMENT", "INSTRUMENT_NO", "DOC_NUM", "DOC_NO", "SEQ", "NUM"
    ]
    candidate_idxs = [(name, upper.index(name)) for name in candidate_names if name in upper]
    print(f"candidate_key_fields={[name for name, _ in candidate_idxs]}")

    for row in naive_reader:
        naive_rows += 1
        naive_widths[len(row)] += 1
        if len(row) != len(naive_header):
            naive_bad_width += 1
            continue
        if parcel_idx is not None:
            pid = row[parcel_idx].strip()
            if pid:
                naive_parcels.add(pid)
            else:
                naive_blank_parcel += 1

    print(f"naive_csv_rows={naive_rows}")
    print(f"naive_bad_width_rows={naive_bad_width}")
    print(f"naive_width_distribution={dict(sorted(naive_widths.items()))}")

    # Physical-line parse: count every source record independently and compare.
    # This does NOT declare the feed safe merely because line count is larger;
    # it records width behavior so any quote-driven merge/split is visible.
    physical_rows = 0
    physical_bad_width = 0
    physical_widths = Counter()
    physical_parcels = set()
    physical_blank_parcel = 0
    sample_rows = []
    bad_samples = []
    parsed_physical = []

    for line_no, line in enumerate(physical_lines[1:], start=2):
        physical_rows += 1
        row = next(csv.reader([line], delimiter=","))
        physical_widths[len(row)] += 1
        if len(row) != width:
            physical_bad_width += 1
            if len(bad_samples) < 10:
                bad_samples.append((line_no, len(row), line[:1000]))
            continue
        parsed_physical.append(row)
        if len(sample_rows) < 5:
            sample_rows.append(row)
        if parcel_idx is not None:
            pid = row[parcel_idx].strip()
            if pid:
                physical_parcels.add(pid)
            else:
                physical_blank_parcel += 1

    print(f"physical_rows={physical_rows}")
    print(f"physical_bad_width_rows={physical_bad_width}")
    print(f"physical_width_distribution={dict(sorted(physical_widths.items()))}")
    print(f"naive_vs_physical_delta={physical_rows - naive_rows}")
    print(f"blank_parcel_rows_physical={physical_blank_parcel}")
    print(f"unique_parcel_ids_physical={len(physical_parcels)}")
    for i, row in enumerate(sample_rows, 1):
        print(f"sample_row_{i}={row}")
    for line_no, row_width, raw in bad_samples:
        print(f"bad_sample line={line_no} width={row_width} raw={raw!r}")

    # Cross-feed integrity against verified parcel production.
    matched = orphan = 0
    if parcel_idx is not None:
        conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE sales_probe_parcel_ids (parcel_id text PRIMARY KEY) ON COMMIT DROP")
                buf = io.StringIO("".join(f"{pid}\n" for pid in sorted(physical_parcels)))
                cur.copy_from(buf, "sales_probe_parcel_ids", columns=("parcel_id",))
                cur.execute("SELECT count(*) FROM sales_probe_parcel_ids s JOIN polk_parcel_v2 p USING (parcel_id)")
                matched = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sales_probe_parcel_ids s LEFT JOIN polk_parcel_v2 p USING (parcel_id) WHERE p.parcel_id IS NULL")
                orphan = cur.fetchone()[0]
            conn.rollback()
        finally:
            conn.close()
    print(f"matched_unique_sales_parcel_ids={matched}")
    print(f"orphan_unique_sales_parcel_ids={orphan}")

    # Candidate uniqueness diagnostics, computed only on clean-width physical rows.
    # We intentionally test combinations instead of selecting a production key here.
    if parcel_idx is not None and candidate_idxs:
        for name, idx in candidate_idxs:
            seen = set()
            dup = blank = 0
            for row in parsed_physical:
                pid = row[parcel_idx].strip()
                val = row[idx].strip()
                if not pid or not val:
                    blank += 1
                    continue
                k = (pid, val)
                if k in seen:
                    dup += 1
                else:
                    seen.add(k)
            print(f"key_test parcel_id+{name}: unique={len(seen)} duplicate_rows={dup} blank_component_rows={blank}")

        # Pairwise candidate combinations, useful where sale_date alone repeats.
        for i in range(len(candidate_idxs)):
            for j in range(i + 1, len(candidate_idxs)):
                n1, idx1 = candidate_idxs[i]
                n2, idx2 = candidate_idxs[j]
                seen = set()
                dup = blank = 0
                for row in parsed_physical:
                    pid = row[parcel_idx].strip()
                    v1 = row[idx1].strip()
                    v2 = row[idx2].strip()
                    if not pid or not v1 or not v2:
                        blank += 1
                        continue
                    k = (pid, v1, v2)
                    if k in seen:
                        dup += 1
                    else:
                        seen.add(k)
                print(f"key_test parcel_id+{n1}+{n2}: unique={len(seen)} duplicate_rows={dup} blank_component_rows={blank}")

    # Probe is deliberately fail-closed on structural drift. A nonzero naive/physical
    # delta is not automatically fatal by itself; it is reported so we can inspect
    # whether physical records are the correct source boundary as with legal.
    if parcel_idx is None:
        raise SystemExit("sales schema has no PARCEL_ID column")
    if physical_blank_parcel:
        raise SystemExit(f"sales key failure: {physical_blank_parcel} physical rows have blank PARCEL_ID")


if __name__ == "__main__":
    main()
