# County integration evidence — October 12, 2026

Current acquisition work is Lehigh Acres / Lee and Port Charlotte / Charlotte.
The worker resolves county-native parcel IDs, queries full polygons for flood
and wetlands, checks road attributes, and writes mapped ROW metrics separately
from recorded legal evidence. New code does not equate proximity with access.

## Deployment order

1. `county_sources.sql`
2. `gis_query.sql` (replaces prior helper; proper encoding, GET/POST by query size)
3. `lee_gis_queue.sql` (existing polygon validator/legacy worker)
4. `frontage.sql` (PostGIS 3.3.7, EPSG:26917)
5. `county_preflight.sql`
6. `county_gis_queue.sql`
7. `qualification_gate.sql`
8. `county_dd_bridge.sql`
9. `county_automation.sql`

Keep the operational SQL outside migration history until a reviewed migration is
generated. All new entry points are SECURITY INVOKER in the private schema;
anonymous/authenticated execution is revoked. New private tables have RLS and
no public grants. Security advisors report the expected no-policy INFO for these
backend-only tables; an existing Auth password-protection warning is unrelated.

## Live verification

| Check | Verified result |
|---|---|
| Lee pending parcel | `334426L3030160020` / 1102 Floyd Ave S |
| Lee parcel identity | OBJECTID 753588; FOLIOID 10642273 |
| Lee latest combined cron run | FEMA X/minimal flood hazard; no mapped wetlands; four paved candidates |
| Lee mapped ROW metric | 162.58 feet of aggregate boundary overlap, **not recorded frontage** |
| Lee plat reference | County GIS legal description: Unit 3, Block 16, PB 15 PG 100, Lot 2 |
| Charlotte validation candidate | `402227279013` / 4239 Conway Blvd / approx. 0.23459 GIS acres |
| Charlotte screening | AE coastal floodplain, one mapped wetland feature, two paved candidates |
| Charlotte qualification | REJECTED_FLOOD_ZONE, no enrichment lead |
| App DD integration | Both records stored as qualification_hold; no deep DD completion |
| Positive lead test | Synthetic qualifying fixture created exactly one held lead; second call created zero; all rolled back |
| Cron combined worker test | Job 14 succeeded at 19:30 UTC; temporary job removed |
| Daily automation | Existing job 11 active at 07:15 UTC, calling run_county_automation(2,false) |

Diagnostic exact-ID polygon and spatial road queries passed for Lee, Charlotte,
Marion, Highlands, Polk, and Sarasota. Some sampled polygons had zero nearby
roads; this is unresolved coverage/access, not failure or clearance. Highlands
requires OBJECTID_1, not OBJECTID. Sarasota long polygon GET returned 404; POST
passed. These are sample connector checks, not countywide qualification proof.

FEMA remains intermittently unavailable. Errors are saved and cannot clear a
parcel; later successful checks replace the current decision on refresh.

## Exact frontage and access: actual remaining gate

County GIS shows Lee FRONTAGE=0; this is not usable exact-frontage evidence.
The mapped ROW features include a `LCEC 60' EASEMENT` label and null recording
references. They do not establish this lot's enforceable ingress/egress.
The recorded plat/deed/easement and current restrictions must be reviewed.
The private parcel_access_reviews table requires a document URL/hash,
instrument, positive recorded length, reviewer, review date/expiry, parcel match,
and explicit access/paved-frontage/current-restrictions confirmations.
No real access review was approved.

Lee Landmark search returned Access Denied after the disclaimer request.
Charlotte's plat viewer uses reCAPTCHA. No challenge was bypassed.
Research entry points:

- https://or.leeclerk.org/LandMarkWeb/
- https://recording.charlotteclerk.com/Search/PlatsCondos
- https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DOT_Engineering/MapServer/10

Osceola's official parcel and NGCS road hosts failed certificate verification
from both pgsql-http and curl. A hosted 2025 parcel archive exists, but it does
not establish current parcel rights or pavement. No Osceola connector was
marked verified and TLS verification was not disabled.

## Qualified-lead readiness

The automation now evaluates fresh environmental, recorded-access, distress,
zoning, and comparable-sale prerequisites, then creates a held contact-enrichment
lead only when all pass. It creates no phone/email enrichment or outreach.
County-specific preflight consumes verified, fresh same-county land sales,
three distinct non-subject parcels, positive prices, and comparable acreage.
It does not reuse Polk's numeric-only IDs for Lee/Charlotte.

The current Lee lot lacks recorded approval and verified distress/zoning/comps.
Zero real parcels are qualified by the new gate. The code path is deployed and
tested; producing qualified deals still needs those source facts. Existing
seller DD/transaction-release safeguards remain in force.

## Tests

SQL scripts: gis_query_test, lee_gis_queue_test, frontage_test,
county_screening_test, qualification_gate_test, county_preflight_test,
county_connector_smoke, county_lead_materialization_test.
Lead test fixtures roll back. Existing Node GIS regression remains applicable.

Roll back scheduling by deactivating job 11 with cron.alter_job(11,active:=false).
Do not drop evidence or broaden contact-release permissions to get a pass.

## October 12 follow-up: recorded documents, Osceola, Lee sales

Deployed `lee_sales_evidence.sql`, updated the endpoint allowlist and combined
scheduler. The official authoritative Lee view (ArcGIS item
`a69c335ad6e54fc0872f7ed47208f11e`, owner `LeeCountyFLGIS`) is synchronized
nightly from LeePA. Its public endpoint requires no key. The collector binds
STRAP, stores source attributes, filters comparable acreage and matching zoning,
vacant-at-sale, qualified transfer codes 01/02 (including override), positive
price, two-year sale window, and <=2km geodesic distance. It deduplicates clerk
instruments. Code 05 bulk prices are excluded. These are rough comps, not an
appraisal, marketability, zoning entitlement or buildability approval.

The live held parcel `334426L3030160020` returned RS-1 and 186 distinct filtered
sales. Its owner mailing city is Lehigh Acres and sale dates are null. No verified
seller-distress signal was created from those attributes. `seller_indicators`
remain REVIEW_REQUIRED. Verified lis-pendens/tax evidence still requires current
records bound to the current parcel and owner; missing data is not a negative
search certificate.

Osceola now uses the official Property Appraiser host `paags.property-appraiser.org`
with TLS verification enabled: `FabricParcel/MapServer/0` (native `PARCELID`, not
space-padded `strap`) and `APPDATA_DBO_streetcenterlines/MapServer/0`. Live exact
polygon and spatial road queries passed from Supabase. The original county host
certificate was not repaired. Road surface/maintenance attributes are absent in
this replacement, so surface remains REVIEW. No archived 2025 polygon is used
as current coverage.

`recorded_document_review.py` processes authorized PDF downloads with text
extraction and OCR fallback, SHA-256, page-indexed candidate excerpts and explicit
REVIEW_REQUIRED. Backend `stage_recorded_document(jsonb)` stores the result only
under research and cannot approve access. Local CLI ingestion is ready; it is
not a running remote document-download worker. Required executables: pdfinfo,
pdftotext, pdftoppm and tesseract. Download access remains unresolved. Lee Clerk
publishes a customer FTP route for daily images, indices and plats. Obtain an
authorized download/FTP account or title-vendor entitlement through its normal
process; no stealth, residential proxy, CAPTCHA-solving service or TLS disabling.
Do not put credentials in git or chat. For the held lot request PB 15 PG 100,
Unit 3, Block 16, Lot 2 plus applicable subsequent access/restriction records.

Live combined run at 20:58 UTC: GIS processed 1; market refreshed 1/failed 0;
qualification evaluated 1/qualified 0; DD synced 2; leads created 0/outreach 0.
Remaining holds: RECORDED_FRONTAGE_AND_ACCESS_REVIEW, SELLER_DISTRESS. The nine
prior Lee flood rejections were not promoted or reopened. Charlotte validation
lot remains rejected. Daily combined job remains 07:15 UTC.

Fresh tests: Lee comp inclusion/exclusion assertions; Osceola exact identity and
spatial roads; recorded document intake rollback and rejection of approved
payload; Python extraction tests; existing preflight, qualification and lead
materialization rollback tests; Node GIS regression. No real recorded-document
OCR/approval, positive Lee distress or fully qualified lead was demonstrated.

For a third-party distress feed, ATTOM documents foreclosure, recording date,
instrument number and delinquent amount in `/saleshistory/expandedhistory`:
https://api.developer.attomdata.com/docs . An ATTOM API key with the applicable
foreclosure entitlement would be required. No key was supplied for this work;
this provider is researched, not live-integrated. Historical foreclosure entries
must not automatically prove current distress or absence of a release. Lee Clerk
bulk recording data/images list $3,520/year; do not purchase automatically.

Final worker rerun after initialization-order fix at 21:00 UTC gave the same
1 refreshed, 0 failures, 0 qualified/created result. New candidates now get a
preliminary qualification result and DD shell before market evidence is saved.
