# Real Lee County parcel geometry worker

`run_lee_parcel_geometry_worker.py` fetches actual polygon geometry from the Lee County `ParcelAddress/MapServer/0` ArcGIS GeoJSON service, normalizing STRAP values like `01-44-26-L1-01003.0020` into `014426L1010030020`. It validates the response, joins staging records by `parcel_id`, and writes only to the **private staging** table `god_mode_ops.lee_parcel_geometry_evidence`.

It deliberately does **not** infer flood zones, wetlands percentages, tax delinquency, ownership, recorded legal access, or seller qualifications. Missing or invalid geometry remains unverified. It never updates the FEMA, USFWS, or Lee Tax Collector job trackers. `PENDING_SCREENING` remains unchanged.

## Invocation

Install `requests>=2.32,<3`, `shapely>=2,<3`, `psycopg2-binary>=2.9,<3`.

- Public GIS smoke test (no DB credentials or writes): `python run_lee_parcel_geometry_worker.py --parcel-id 01-44-26-L1-01003.0020`
- Staging queue dry run: set `STAGING_SUPABASE_DB_URL` and run `python run_lee_parcel_geometry_worker.py --limit 25`
- Explicit staging write: `python run_lee_parcel_geometry_worker.py --limit 25 --write` (requires same staging URL)
- Tests: `python -m unittest discover -s tests -p 'test_lee_parcel_geometry_worker.py' -v`

GitHub Action `Lee County real parcel geometry (staging only)` is **manual** and defaults to dry run. To use staging write mode, configure repository secret `STAGING_SUPABASE_DB_URL` **specifically for staging project `ltwqgkcblvtscgldxvjw`** and then set `write_to_staging=true`. Do not reuse an unverified or production-oriented `SUPABASE_DB_URL` secret. Optionally configure `SUPABASE_DB_CA_CERT` for certificate verification.

The migration `supabase/migrations/20261107000100_lee_parcel_geometry_evidence.sql` is idempotent and documents the private landing schema already created in staging. The original script's MD5/digit-based synthetic data generation and `'now()'` string passed as a SQL timestamp were removed.

**Operator checks:** after a staging run, compare `COUNT(*)` from `god_mode_ops.lee_parcel_geometry_evidence` to the `LEE_PARCEL_GEOMETRY` job's `parcels_processed`. A polygon is **mapping evidence only** and is not a verified legal boundary or a basis for contact enrichment.


## Full Lee GIS staging ingestion and FEMA NFHL pilot

The geometry worker supports `--all --limit 500 --write` for keyset-paginated
full staged ingestion. Each batch only saves polygons with matching county STRAP,
valid source OBJECTID, well-formed WGS84 polygon geometry, and source URL.
STRAPs with no polygon match are skipped and remain unresolved, not padded.

The GitHub Actions workflow `Lee County verified GIS and FEMA staging` can be
manually dispatched as `geometry-full`, `geometry-batch`, or `fema-pilot`. A
daily scheduled geometry run is defined, but GitHub only activates it once
the workflow is merged into the repository's default branch. Both writers
require the **staging-only** `STAGING_SUPABASE_DB_URL` secret pointing to
`ltwqgkcblvtscgldxvjw`; production URLs are rejected.

`run_lee_fema_nfhl_worker.py` queries official FEMA NFHL Flood Hazard Zones
(MapServer layer 28) using a *parcel envelope* to retrieve candidate hazard
polygons and uses Shapely and pyproj for precise local polygon intersections.
It calculates SFHA overlap percentage from deduplicated intersections, keeps
mixed zones and any high-risk overlap in REVIEW_REQUIRED, and persists only
to `god_mode_ops.lee_parcel_fema_intersections`. If FEMA is unavailable or
a source response is incomplete, no flood-zone record is created.

Run `python -m unittest discover -s tests -p 'test_lee*worker.py' -v`
for the test suite. Staging migration 20261107000200 creates the private
FEMA result table; it does not change the main evidence cache, outreach,
or qualification status.
