# Strict feed evidence probe

Run `python probe_feed_evidence.py parceltax` or `python probe_feed_evidence.py permit`.
Run `python -m unittest discover -s tests -v` for the offline failure-gate tests.

This separate, read-only probe uses anonymous FTPS with certificate verification,
checks remote size and modification time before and after transfer, and retains
the archive plus a SHA-256 and JSON inspection report under the ignored
`.polk_import/evidence/` directory. It never reads Supabase credentials or writes
to production. Do not commit these archives or raw property samples.

An existing archive can be inspected with `--zip PATH`. That mode explicitly
does not attest official origin; match the archive hash to the download evidence.
Strict UTF-8 decoding is the default. An explicit `--encoding` overrides it and
is recorded, but must be supported by source evidence before ingestion.

The probe requires one ZIP data member, unique nonblank column names, a
PARCEL_ID column, nonempty data, valid CSV, consistent widths, and nonblank
parcel IDs. Disk-backed SQLite checks candidate keys after stripping outer
whitespace. Candidate field names are tested only when present in the actual
header. Blank and duplicate keys cannot be approved. Full field lengths,
blanks, and outer-whitespace counts are recorded without logging property rows.

`READY` here means a structurally valid file with a complete unique candidate
key. It does **not** approve field types, business semantics, freshness,
inventory joins, source-to-production equality, or valuation use.
`production_verified` and `parcel_inventory_reconciled` remain false.
Any ingestion contract must separately establish types and canonical key
normalization (including collisions after integer casts), acceptable source
changes, and reconciliation requirements. A probe does not mark a feed VERIFIED.

## Findings from September 13, 2026

The source repository inspected was commit
`fa7643df62db1ee834c23d736a3f5982cf62b59b`.

- Supabase management reported ACTIVE_HEALTHY, while a fresh SQL query failed
  with `57P03: the database system is not accepting connections; Hot standby
  mode is disabled`. This is a confirmed connectivity obstruction, not a
  diagnosis of its infrastructure cause. Production counts remain unverified.
- Sales run [34742320875](https://github.com/lili2ana/god-mode-polk-importer/actions/runs/34742320875)
  validated 3,031,366 source rows and copied shards 00 through 23 before COPY
  failed with ReadOnlySqlTransaction. The log initially reported writable
  PostgreSQL. No completed promotion was established.
- Parcel-tax run [34747523638](https://github.com/lili2ana/god-mode-polk-importer/actions/runs/34747523638)
  reported 2,852,211 rows, 438,623 parcel IDs, 11 columns, and no duplicate
  (PARCEL_ID, TAXDIST) pairs. Its probe did not test LNNUM and used replacement
  decoding; the new probe addresses both limitations.
- A fresh local FTPS download and full strict probe completed at
  2026-09-13T17:41:48Z: 2,852,211 rows, 438,623 parcels, no malformed rows,
  no blank fields, and both (PARCEL_ID, LNNUM) and (PARCEL_ID, TAXDIST) complete
  and unique. SHA-256:
  `c6e85bcfffba2fcb90ea5853cae75d5efd155ba4ad48de57e44224c8365d87c7`.
  Source MDTM was `20260911093513`; the archive was 33,771,176 bytes.
  This establishes structural readiness only; production remains unverified.
- The official permit download was 15,189,272 bytes and contained an
  18-column `FTP_CAMA/ftp_permit.txt`. Archive SHA-256:
  `118f9b488947b25b15796a597b0910cc6063c834225b00df64779d359ad141c7`.
  Strict CSV parsing rejected physical line 71, where DSCR contains an
  unescaped inch quotation mark. Date samples include `1899-12-30 00:00:00`.
  Production permit ingestion must wait for a documented parsing/quarantine
  policy and a verified interpretation of those dates. Do not silently repair
  quotes or treat the sample as a complete-file validation.

## Sales recovery work still required

These are findings in the existing loader, not fixes made by this probe:

1. Enforce the database preflight result instead of merely printing it, and
   check target identity in the loader itself before writes.
2. Require strict CSV and real calendar-date validation. Regex shape checks
   cannot establish date validity. Check uniqueness after production type casts.
3. Protect staging across all possible writers and bind it to a source hash.
   The current runner truncates staging on restart and lacks resume evidence.
4. Reconcile keys and field values, not only total row counts. Prefix commits
   can expose partial production changes; consumers must require an identified,
   verified snapshot and must not rely on a stale verified marker.
5. Treat 3,031,366 as the known snapshot count. A fresh official feed can change;
   require a new accepted manifest rather than bypassing the count gate.
6. Establish available database storage/compute and recovery state before
   another bulk run. Preserve staging evidence while investigating the outage.
7. Implement the requested verified staging cleanup; the current loader does
   not clear staging after success.

Tax district amounts do not establish delinquency. The observed parcel-tax
header lacks a tax year or payment-status field. Delinquency needs separate
Tax Collector evidence and temporal context; see the
[official explanation](https://www.polktaxes.com/services/delinquency-and-tax-sale-information/).
Seller outreach remains outside this probe and no outreach is triggered.
