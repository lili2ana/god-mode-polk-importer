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
