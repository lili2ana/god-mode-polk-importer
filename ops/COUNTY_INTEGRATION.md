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
