#!/usr/bin/env python3
"""Read-only audit of an accepted local sales ZIP; no download or database writes."""
import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from sales_contract import HEADER, normalize

def audit(path, accepted_hash, expected_rows):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != accepted_hash:
        raise ValueError("Archive fingerprint differs from accepted source")
    all_rows, recent, missing_recent_keys = Counter(), Counter(), set()
    date_min, date_max = {}, {}
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) != 1:
            raise ValueError("Expected one source member")
        with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="strict", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            if next(reader) != HEADER:
                raise ValueError("Header drift")
            for source in reader:
                row = normalize(source)
                shard = int(hashlib.sha256(row[0].encode()).hexdigest()[:16], 16) % 32
                all_rows[shard] += 1
                if row[3]:
                    date = datetime.strptime(row[3], "%m/%d/%Y").date().isoformat()
                    date_min[shard] = min(date_min.get(shard, date), date)
                    date_max[shard] = max(date_max.get(shard, date), date)
                    if date >= "2022-01-01":
                        recent[shard] += 1
                        if shard >= 24:
                            key = row[0], row[2]
                            if key in missing_recent_keys:
                                raise ValueError("Duplicate recent key in missing source shards")
                            missing_recent_keys.add(key)
    if sum(all_rows.values()) != expected_rows:
        raise ValueError("Source count differs from accepted snapshot")
    return {"source_sha256": digest, "source_rows": sum(all_rows.values()),
            "missing_shards": list(range(24, 32)),
            "missing_history_rows": sum(all_rows[s] for s in range(24, 32)),
            "missing_recent_rows_all_property_types": sum(recent[s] for s in range(24, 32)),
            "shards": [{"shard": s, "rows": all_rows[s], "recent_rows": recent[s],
                        "oldest": date_min.get(s), "newest": date_max.get(s)} for s in range(32)],
            "residential_reconciliation_verified": False, "production_verified": False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--accepted-sha256", required=True)
    parser.add_argument("--expected-rows", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.zip, args.accepted_sha256, args.expected_rows), indent=2))

if __name__ == "__main__":
    main()
