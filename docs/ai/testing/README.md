# Verification

Run SQL assertion scripts in ops after applying their implementation scripts.
Run county_connector_smoke.sql for a diagnostic polygon/road sample per county;
it is not a countywide coverage test. Lead materialization tests use synthetic
records inside a transaction that rolls back; check zero fixture records remain.

Live combined cron test job 14 succeeded at 19:30 UTC on October 12, 2026 and
was removed. Daily job 11 remains at 07:15 UTC. Existing Node GIS regression
tests and final SQL assertions are required before reporting deployment status.

No live positive qualified-lead result exists. No exact recorded frontage or
legal-access approval was validated. No authenticated interactive Edge Function
end-to-end test was added. Capture these limitations in completion reporting.
