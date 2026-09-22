# Sales recovery release

**Superseded for production loading September 22, 2026.** The lean architecture
keeps countywide archives outside PostgreSQL. Do not run the historical countywide
`--load` command below. The GitHub sales workflow now exposes validation only and
receives no database credentials. See `LEAN_PIPELINE.md` for the active direction.
The remainder documents the historical recovery implementation and its tests.

The old load entrypoint now defaults to local source validation. A code push
does not launch a sales production load. Use the manual Load Polk Sales Feed
workflow, which defaults to validate. Publication requires action=load, a
previously validated source SHA-256, its row count, and capacity_reviewed=true.

Local commands:

```
python load_sales_snapshot.py
python check_database_readiness.py --write-probe
python load_sales_snapshot.py --load --accepted-sha256 <accepted-hash> --expected-rows <accepted-count> --capacity-reviewed
```

The source is validated in full before staging changes. Invalid calendar dates,
negative/nonfinite prices, NUL bytes, malformed CSV and collisions after integer
key conversion stop the run. Null dates/prices remain unknown. Price syntax
acceptance does not establish that a sale is suitable as a comparable.

The loader uses direct/session-pooler connections on port 5432 and checks the
exact project identity. It refuses read-only/recovery mode and competing loader
session locks. Existing staging is read and compared to the accepted source;
only identical rows can be resumed. It never truncates staging at startup.
Missing rows are COPY-loaded in committed shards. No automatic retry obscures
an uncertain commit: rerun against the same accepted source and reconcile.

Publication holds write locks, reconciles all staging keys and fields, merges
prefixes inside one transaction, reconciles all production keys and fields,
writes the VERIFIED marker, and clears staging in that same transaction.
An extra/stale production key is a hard failure, not an implicit deletion.
Readers see the preceding committed production state until the new transaction
commits. Other writers using old revisions must be stopped before operation;
an advisory lock only coordinates clients that honor it, and final table locks
protect the publication transaction. Partial staging remains untrusted.

Capacity review is a real prerequisite: an atomic publication retains WAL and
locks for its transaction, and verification reads all rows. Evaluate storage,
WAL headroom, statement duration and production load before enabling it. This
release does not diagnose or repair the Supabase infrastructure outage.

The observed source snapshot validated locally on September 14, 2026:
3,031,366 rows; SHA-256
`83f0d1021c6ac1fdec74cbad1fd9896058c85a631c42fa98d7f3add4409fed72`.
The source count remains an accepted snapshot parameter, not a permanent
assumption about tomorrow's feed. New hashes need validation and acceptance.
Local source archives/SQLite files remain in ignored `.polk_import/` for recovery.
Do not publish these raw files into the public GitHub repository or artifacts.

Integration tests use a disposable PostgreSQL 17 container at localhost:55432.
They verify partial-stage resume, lock contention, same-count data corruption,
extra production rows and rollback when writing the VERIFIED marker fails.
These tests validate the implementation; the real production schema and
resource capacity still require checks once Supabase recovers. They do not
constitute production verification or parcel-inventory reconciliation.

Tax typing, permit quarantine, property joins, private UI, valuation calibration
and ranked deals remain downstream. Seller outreach stays disabled.
