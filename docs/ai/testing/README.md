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

Follow-up checks passed: `lee_sales_evidence_test.sql`,
`osceola_connector_test.sql`, `recorded_document_intake_test.sql`, existing
preflight/qualification/lead rollback tests, two Python document parser tests,
and Node screening regression. Live combined worker refreshed the Lee market
snapshot and left the real parcel on only recorded-access and seller-distress
holds. Original Osceola certificate remains invalid; alternate PA GIS transport
passed with verification enabled. Document download credentials and real title
review remain required. OCR fallback has not been exercised on a real clerk PDF.


Current follow-up: 10 Python tests passed, including authenticated loopback
HTTP upload with real PDF extraction, TIFF conversion, APN/FIPS mismatch and
historical-distress candidate handling. New SQL distress threshold assertions
and live Osceola network check passed. Live combined re-evaluation at 21:15 UTC
on October 12 processed the single active Lehigh hold: comps pass, frontage/access
and distress still missing, 0 qualified/created. Port Charlotte has no active
hold; its prior flood rejection stays rejected. Credentialed FTP/ATTOM retrieval,
remote workflow and database connection from that workflow remain unverified.

Final SQL rollback verified that a submitted VERIFIED distress payload is
stored as REVIEW_REQUIRED, leaving zero synthetic distress records. Anonymous
intake execution and authenticated table reads both returned false. Advisors
reported expected private-table RLS/no-policy information and the pre-existing
unrelated Auth password-protection warning; no public grants were added.

October 13 ATTOM fix: 14 local and GitHub runner tests pass. Live run
38348728722 completed successfully. HTTP 400 / SuccessWithoutResult is now a
valid no-match response; authentication and other errors remain errors. Address
fallback matched the Lehigh parcel and county, but returned zero nonempty adverse
records. Empty foreclosure objects no longer count as evidence. Foreclosure
entitlement remains unverified without a known positive record.

The first credentialed runner connection to the Supabase pooler failed certificate
verification. Final run explicitly skips DB persistence without SUPABASE_DB_CA_CERT;
it does not downgrade TLS or claim a qualification rerun. Download the project
root CA from Database settings and supply the certificate before credentialed
persistence. FTP request W114375-101226 remains pending.

October 14 certificate follow-up: user-supplied Supabase Root 2021 CA validates
as a self-signed CA, expires April 26, 2031, SHA256 fingerprint
807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa.
Bundled public trust anchor is used by the workflow (optional supplied CA secret
can override). TLS verify-full remains enabled. Live GitHub run 38524764537
passed 14 tests, ATTOM request, candidate persistence and full county automation.
Provider failures 0; GIS processed 1, market refreshed 1, qualification evaluated
1/qualified 0, DD synced 2, leads/outreach 0. October 14 06:52 UTC database readback
shows ATTOM address lookup saved with 0 adverse records and Lehigh HOLD for
recorded access/frontage and seller distress. Certificate transport blocker is
resolved; real foreclosure entitlement positive control remains unproved.
