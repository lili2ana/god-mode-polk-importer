# GIS queue deployment — October 12, 2026

This records the earlier Lee-only deployment. The later combined county worker
and current job-11 command are documented in [COUNTY_INTEGRATION.md](COUNTY_INTEGRATION.md).

The scheduled Lee worker now retrieves a fresh parcel polygon for each pending
candidate, verifies its identity, and saves FEMA, NWI, and road evidence. It does
not depend on historical geometry evidence and needs no interactive operator
session or GOD_MODE_OPERATOR_TOKEN. The interactive Edge Function still uses
its existing Supabase operator-session authorization.

Apply `gis_query.sql`, `gis_query_test.sql`, `lee_gis_queue.sql`, and
`lee_gis_queue_test.sql` in that order. Then apply `lee_gis_schedule.sql`.
These are private operational deployment scripts, not Supabase migration history.
Functions use SECURITY INVOKER and revoke public/anonymous/authenticated execution.
The schema is already private. The HTTP helper accepts only four fixed endpoints,
uses proper URL encoding, an 8-second request timeout, and at most two attempts.
Truncated, malformed, or failed responses never become a clear result.

The existing `god_mode_lee_full_parcel_gis_daily` job (11) is active at `15 7 * * *`
(07:15 UTC). Each run handles two pending Lee parcels, oldest refresh first, and
skips evidence refreshed within 20 hours. Calls are capped at five parcels and
protected by a transaction advisory lock. Errors remain evidence for retry on
the next eligible run. Rejected candidates are not requalified or selected.

## Verified live

- Manual run processed pending parcel `334426L3030160020`.
- Boundary resolved to county OBJECTID 753588 / FOLIOID 10642273.
- FEMA returned HTTP 200: X / AREA OF MINIMAL FLOOD HAZARD / SFHA_TF F.
- NWI returned HTTP 200 and zero mapped wetland features.
- Roads returned HTTP 200 and four paved candidates within a 30m polygon buffer.
- All four evidence records were read back.
- Repeat normal run processed zero, proving the freshness filter.
- Temporary cron job 13 ran at 18:11 UTC and succeeded; its four saved results
  were read back. The temporary job was removed. Existing daily job 11 remains.
- SQL assertions reject invalid polygons, truncated responses, unexpected
  endpoints, and public execution privileges.

FEMA POST failed with a connection reset during diagnosis; properly encoded GET
queries succeeded in the manual and cron runs. This demonstrates current
successful execution, not guaranteed FEMA uptime.

## Remaining gates

Roads are still candidates only. Exact recorded frontage and legal access are
unresolved; screening/contact statuses are not automatically cleared. Other
county integrations are not deployed by these scripts. The current queue has
one pending Lee candidate and nine flood-rejected Lee candidates.

Polk metadata/probe found PARCELID and PR_STRAP can differ. For example, GIS
PARCELID `303104994100031709` appeared with PR_STRAP
`30-31-05-994000-031709`. A normalized PR_STRAP must not be silently treated as
GIS PARCELID. A county identity join must be checked before screening.

To disable this worker without disturbing evidence, set job 11 inactive using
`cron.alter_job(11, active := false)`. No public endpoints or new subscriptions
were created.
