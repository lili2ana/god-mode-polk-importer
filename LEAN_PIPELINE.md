# Lean operational pipeline

The September 22 property-profile release is recorded in `PROPERTY_RELEASE.md`.
It adds private remote source archival with tested restores, parcel/owner/legal
snapshots and the deployed target-refresh correction. The earlier tax evidence
below remains an offline snapshot and has not been promoted to production.

Countywide production loading is superseded. Keep raw county data outside the
operational database, prove reduced snapshots alongside existing tables, and only
then switch readers. No existing countywide table or staging data is deleted by
this change. The sales workflow validates only; both sales CLI entry points reject
`--load` before filesystem, network or database access. Historical loader internals
remain covered by integration tests, but are not the current publication path.

## Verified September 22, 2026

Read-only production queries found 36,713 valid, unique target parcel keys with
zero drift against `public.properties`. Six retention policies have cleanup OFF.
Anonymous and authenticated roles have no SELECT grants on the two ops tables.
These private tables currently do not have RLS enabled; review defense in depth
before adding any access grants or exposing the schema.

Normalized target joins found 36,713 parcel, 56,295 owner and 97,754 legal rows.
The refresh cron is `27 5 * * *`, with `cron.timezone=GMT`, not local Florida time.
A ten-minute offset does not prove the preceding refresh finished. The current
refresh function can retain a stale registry entry after a property's parcel ID
becomes blank. The new exporter fails on that drift rather than silently exporting
it. Changing the refresh function and adding completion-based orchestration remain
separate deployment work.

The first **offline** parcel-tax run processed the accepted September 11 archive:

- Source SHA-256: `c6e85bcfffba2fcb90ea5853cae75d5efd155ba4ad48de57e44224c8365d87c7`
- Source rows: 2,852,211; selected rows: 236,698 (8.30% retained).
- All 36,713 target keys matched; none missing.
- Local SQLite snapshot: 55,574,528 bytes; this is not a PostgreSQL size estimate.
- Original ZIP: 33,771,176 bytes, copied and checked by SHA-256 before and after processing.
- Production verification: false. Durable remote archive verification: false.

This is an accepted historical snapshot, not a claim about today's latest county
file. The next source version requires a new probe, fingerprint and row count.
`TAXESDUE` means tax context here; it does not establish delinquency or payment
status. Rows are tax-district records; this file does not establish multiple tax
years. Preserve this distinction in downstream scoring.

## Keep / move out / replace

| Component | Direction | Gate |
| --- | --- | --- |
| Target properties, leads, deals, DD, scores, owner context | KEEP in operational PostgreSQL | Existing application behavior preserved |
| Supabase auth, Edge Functions, cron, dashboard integrations | KEEP pending dependency audit | PostgreSQL compatibility alone does not migrate these services |
| Full county ZIP/TXT and historical snapshots | MOVE OUT to private archive storage | Immutable keys, hashes, retention, access control and restore test |
| Parcel/owner/legal countywide hot copies | REPLACE reads with target snapshots | Key, row and field equivalence; downstream tests; archived originals |
| Sales archive | MOVE OUT; build separate comparable-sale index | Property type, geography, date, transaction qualification and coverage validated |
| Tax data | Target context plus independently verified distress signals | No delinquency inference from TAXESDUE alone |
| Permit data | Target history and evidence-linked features | Resolve malformed source quoting and sentinel-date semantics first |

The previous measured database footprint was about 1.47 GB, including roughly
556 MB in sales/legal staging and 616 MB in countywide parcel/owner/legal tables.
Scaling those three tables by measured target-row fractions suggests roughly
53 MB for reduced copies and roughly 350 MB for the remaining database after a
future fully verified retirement of old copies. This is a rough planning estimate,
not a capacity promise: row widths and indexes differ; tax snapshots, comps, WAL,
system files, growth and simultaneous old/new copies add space. Measure actual
operational tables and comp coverage before deciding whether any paid provider
or migration is necessary. No paid service is provisioned here.

## Run privately

Use a trusted private workspace. `.polk_import/` is git-ignored. Do not publish raw
archives, target registries or filtered property data as public GitHub artifacts.

```powershell
python export_target_registry.py --output .polk_import/lean/targets.json
python build_target_tax_snapshot.py --zip <accepted-archive.zip> --targets .polk_import/lean/targets.json --output <new-private-directory> --accepted-sha256 <accepted-hash> --expected-rows <accepted-count>
```

The exporter uses `SUPABASE_DB_URL`, validates the project and TLS, and reads
properties, registry and policies in one repeatable-read, read-only transaction,
then rolls back. The filter needs no database credentials. It requires an export
at most 24 hours old, strict schema/CSV/UTF-8, accepted source hash/count, unique
normalized source keys, validated numeric syntax and full target coverage. Even
non-target rows are validated. Raw source values are retained in the local payload.
Failed runs have a BLOCKED manifest and no final snapshot; retry into a new directory.

Current local archive copies are not a durable warehouse deployment. Before any
scheduled production pipeline or reader cutover: configure private durable archive
storage, test restore by hash, add parcel/owner/legal contracts from probed official
headers, define a defensible comp universe, build shadow operational tables after
capacity review, reconcile source/target keys and fields, and test application
reads. Recheck target-generation identity immediately before publication so a
changed universe cannot be silently published. Outreach and destructive cleanup
remain OFF.
