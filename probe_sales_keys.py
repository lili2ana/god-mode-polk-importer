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
EXPECTED_HEADER = [
    "PARCEL_ID", "SALE_ID", "LN_NUM", "SALEDT", "PRICE", "BOOK", "PAGE",
    "SALETYPE", "TRNS_CD", "TRNS_DSCR", "INSTRTYP", "INSTRTYP_DSCR",
    "GRANTOR", "GRANTEE", "FORECLOSURE",
]


def normalize_header(row):
    return [c.strip().strip('"').lstrip("\ufeff") for c in row]


def open_text_member():
    zf = zipfile.ZipFile(OUT)
    members = [i for i in zf.infolist() if not i.is_dir()]
    if not members:
        zf.close()
        raise RuntimeError("sales ZIP contains no data file")
    raw = zf.open(members[0])
    text = io.TextIOWrapper(raw, encoding="cp1252", newline="")
    return zf, raw, text


def iter_rows():
    zf, raw, text = open_text_member()
    try:
        reader = csv.reader(text, delimiter=",")
        header = normalize_header(next(reader))
        if header != EXPECTED_HEADER:
            raise RuntimeError(f"sales header drift: {header}")
        for row in reader:
            yield reader.line_num, row
    finally:
        text.close()
        raw.close()
        zf.close()


def key_test(name, idxs):
    seen = set()
    dup = blank = rows = 0
    for _, row in iter_rows():
        rows += 1
        if len(row) != 15:
            raise RuntimeError(f"logical CSV width drift during {name}: {len(row)}")
        vals = tuple(row[i].strip() for i in idxs)
        if any(not v for v in vals):
            blank += 1
            continue
        if vals in seen:
            dup += 1
        else:
            seen.add(vals)
    print(f"key_test {name}: rows={rows} unique={len(seen)} duplicate_rows={dup} blank_component_rows={blank}")
    return len(seen), dup, blank


def main():
    ftp = FTP_TLS(HOST, timeout=120)
    ftp.login()
    ftp.prot_p()
    print(f"remote_size={ftp.size(REMOTE)}")
    with OUT.open("wb") as f:
        ftp.retrbinary(f"RETR {REMOTE}", f.write, blocksize=1024 * 1024)
    ftp.quit()
    if not zipfile.is_zipfile(OUT):
        raise SystemExit("sales feed is not a valid ZIP")

    # First pass: prove logical CSV records reconcile exactly to physical-line delta.
    logical_rows = 0
    bad_width = 0
    multiline_records = 0
    extra_physical_lines = 0
    multiline_span_dist = Counter()
    parcel_ids = set()
    sale_ids = set()
    sale_id_parcel_pairs = set()
    foreclosure_values = Counter()
    previous_end_line = 1  # header ends on physical line 1
    multiline_samples = []

    for end_line, row in iter_rows():
        logical_rows += 1
        span = end_line - previous_end_line
        previous_end_line = end_line
        multiline_span_dist[span] += 1
        if span > 1:
            multiline_records += 1
            extra_physical_lines += span - 1
            if len(multiline_samples) < 8:
                multiline_samples.append((end_line - span + 1, end_line, span, row[12:15] if len(row) == 15 else row))
        if len(row) != 15:
            bad_width += 1
            continue
        pid = row[0].strip()
        sid = row[1].strip()
        if pid:
            parcel_ids.add(pid)
        if sid:
            sale_ids.add(sid)
            if pid:
                sale_id_parcel_pairs.add((sid, pid))
        foreclosure_values[row[14].strip()] += 1

    # Count physical lines independently for exact reconciliation.
    zf, raw, text = open_text_member()
    try:
        physical_lines = sum(1 for _ in text)
    finally:
        text.close(); raw.close(); zf.close()
    physical_data_lines = physical_lines - 1

    print(f"logical_csv_rows={logical_rows}")
    print(f"logical_bad_width_rows={bad_width}")
    print(f"physical_data_lines={physical_data_lines}")
    print(f"physical_minus_logical={physical_data_lines - logical_rows}")
    print(f"multiline_records={multiline_records}")
    print(f"extra_physical_lines_consumed_by_multiline_records={extra_physical_lines}")
    print(f"multiline_span_distribution={dict(sorted(multiline_span_dist.items()))}")
    print(f"unique_parcel_ids={len(parcel_ids)}")
    print(f"unique_sale_ids={len(sale_ids)}")
    print(f"unique_sale_id_parcel_pairs={len(sale_id_parcel_pairs)}")
    print(f"sale_ids_are_multi_parcel={len(sale_id_parcel_pairs) > len(sale_ids)}")
    print(f"foreclosure_values={dict(foreclosure_values)}")
    for start, end, span, tail in multiline_samples:
        print(f"multiline_sample start_line={start} end_line={end} span={span} tail={tail!r}")

    if bad_width != 0:
        raise SystemExit(f"logical CSV parser still has {bad_width} bad-width rows")
    if physical_data_lines - logical_rows != extra_physical_lines:
        raise SystemExit(
            f"multiline reconciliation failed: physical-logical={physical_data_lines-logical_rows} "
            f"but spans account for {extra_physical_lines}"
        )

    # Cross-feed parcel integrity using the fully reconstructed logical rows.
    conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE sales_probe_parcel_ids (parcel_id text PRIMARY KEY) ON COMMIT DROP")
            buf = io.StringIO("".join(f"{pid}\n" for pid in sorted(parcel_ids)))
            cur.copy_from(buf, "sales_probe_parcel_ids", columns=("parcel_id",))
            cur.execute("SELECT count(*) FROM sales_probe_parcel_ids s JOIN polk_parcel_v2 p USING (parcel_id)")
            matched = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM sales_probe_parcel_ids s LEFT JOIN polk_parcel_v2 p USING (parcel_id) WHERE p.parcel_id IS NULL")
            orphans = cur.fetchone()[0]
        conn.rollback()
    finally:
        conn.close()
    print(f"matched_unique_sales_parcel_ids={matched}")
    print(f"orphan_unique_sales_parcel_ids={orphans}")

    # Exact uniqueness tests on reconstructed logical records.
    results = {}
    results["SALE_ID"] = key_test("SALE_ID", (1,))
    results["SALE_ID+LN_NUM"] = key_test("SALE_ID+LN_NUM", (1, 2))
    results["PARCEL_ID+SALE_ID"] = key_test("PARCEL_ID+SALE_ID", (0, 1))
    results["PARCEL_ID+SALE_ID+LN_NUM"] = key_test("PARCEL_ID+SALE_ID+LN_NUM", (0, 1, 2))
    results["PARCEL_ID+LN_NUM"] = key_test("PARCEL_ID+LN_NUM", (0, 2))
    results["PARCEL_ID+SALEDT+INSTRTYP"] = key_test("PARCEL_ID+SALEDT+INSTRTYP", (0, 3, 10))

    zero_zero = [name for name, (_, dup, blank) in results.items() if dup == 0 and blank == 0]
    print(f"zero_duplicate_zero_blank_candidates={zero_zero}")
    if orphans:
        raise SystemExit(f"sales parcel integrity failed: {orphans} orphan parcel IDs")
    if not zero_zero:
        raise SystemExit("no tested sales conflict key has zero duplicates and zero blanks")


if __name__ == "__main__":
    main()
