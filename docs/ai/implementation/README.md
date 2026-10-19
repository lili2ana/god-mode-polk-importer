# Implementation

Operational SQL lives in `ops/`. Apply county_sources before the updated
gis_query transport. Apply frontage and county_preflight before the county
worker/qualification gate; apply DD bridge and county_automation last.
The legacy Lee worker remains for compatibility; cron job 11 uses the combined
county worker. No public endpoint or second operator token was introduced.

New private tables: parcel_access_reviews and parcel_qualification_results.
The current queue's Lehigh lot is held; the Charlotte validation candidate is
flood-rejected. Their app DD rows remain qualification_hold. No real qualified
lead or outbound message was produced.


Evidence follow-up: `evidence_pipeline.py` supplies FTPS download, configured
HTTPS aggregator fallback, loopback upload and ATTOM FIPS/APN candidate fetch.
`distress_evidence.sql` deploys private candidate storage and current-record
qualification rules. `osceola_road_network.sql` overlays official hosted county
roads and the JPA maintenance layer, retaining pavement/ROW review. County GIS
queue records this overlay for future Osceola candidates. The GitHub manual
workflow is committed for credentialed runs; it is not a newly enabled daily
remote intake worker. Database cron continues its existing daily qualification.

ATTOM transport handles documented SuccessWithoutResult separately from HTTP
failures, retries a supplied official address only after no match, and binds any
result to normalized APN/FIPS. Source empty foreclosure objects are filtered by
presence of instrument, recording date or distress type. Neither no-match nor
empty adverse records qualify distress. Integration-branch workflow push now
runs the masked live ATTOM check; DB stage requires the project CA certificate.

The public Supabase Root 2021 CA supplied on October 14 is bundled at
ops/certs/supabase-prod-ca-2021.crt. Workflow sets PGSSLROOTCERT to that file;
SUPABASE_DB_CA_CERT remains an optional override. Missing-secret skip was removed.
Verified-TLS GitHub worker persistence and re-evaluation passed live. Original
uploaded certificate was not modified. FTP request remains pending.
