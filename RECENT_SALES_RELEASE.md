# Recent sales shadow — October 5, 2026

The incomplete historical staging table contains 2,270,993 rows. The legacy full-history CLI remains disabled. A private 2022+ residential shadow has been deployed without changing production readers or deleting source data.

## Live verification

- Selected rows: 199,707; canonical primary key (parcel_id, ln_num).
- Distinct parcels: 111,257; observed dates January 1, 2022 through September 2, 2026.
- Symmetric EXCEPT ALL over all 15 mapped fields: zero difference rows against the current staging subset.
- Shadow including indexes: 51,642,368 bytes; legacy staging: 399,147,008 bytes.
- RLS enabled; anon/authenticated SELECT denied; service_role access only.
- No feed_status entry marked verified. No underwriting reader switched. No outreach triggered.

## Remaining gate

Source completeness is unproved: the earlier full-source 2022+ residential probe reported 266,468 rows, while staging yields 199,707. That historical reference is not a hardcoded acceptance count for a new source version. Recover the accepted archived ZIP and manifest, prove its hash and restore, validate the full source once, then reconcile the configured recent residential subset. Retain older history outside the operating database. Transaction qualification and comparable geography/type coverage remain necessary before ARV use.

Only after full-source reconciliation, reader tests and durable archive restore may staging be retired. Current temporary storage has increased by about 49.3 MiB; no storage savings have yet been realized. This migration creates a shadow once; do not rerun it manually on a database where it is already applied.
