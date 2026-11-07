#!/usr/bin/env python3
"""Ingest *real* Lee County parcel geometry into the private staging landing table.

Does not infer flood zone, wetlands, legal access, ownership, or tax delinquency.
Does not update qualification status or the existing FEMA/USFWS/tax jobs.

Usage:
    python run_lee_parcel_geometry_worker.py --parcel-id 01-44-26-L1-01003.0020
    STAGING_SUPABASE_DB_URL=postgresql://... python run_lee_parcel_geometry_worker.py --limit 25
    STAGING_SUPABASE_DB_URL=postgresql://... python run_lee_parcel_geometry_worker.py --limit 25 --write
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

GIS_LAYER = "https://gismapserver.leegov.com/gisserver910/rest/services/Layers/ParcelAddress/MapServer/0"
STAGING_PROJECT = "ltwqgkcblvtscgldxvjw"


class GeometryError(ValueError):
    """Source record was absent, ambiguous, or invalid."""


def normalize_strap(parcel_id: str) -> str:
    """Lee county's STRAP GIS field uses 17 uppercase alphanumeric characters."""
    if not isinstance(parcel_id, str):
        raise ValueError("parcel_id must be a string")
    cleaned = re.sub(r"[-.\s]", "", parcel_id).upper()
    if not re.fullmatch(r"[A-Z0-9]{17}", cleaned):
        raise ValueError("Invalid Lee County STRAP; cannot request authoritative geometry")
    return cleaned


def staging_dsn_is_safe(dsn: str) -> bool:
    """Fail closed if the DSN does not identify the permitted STAGING project."""
    try:
        parsed = urlsplit(dsn)
        if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
            return False
        host = (parsed.hostname or "").lower()
        user = unquote(parsed.username or "")
        return (host == f"db.{STAGING_PROJECT}.supabase.co" or
                (host.endswith(".pooler.supabase.com") and user == f"postgres.{STAGING_PROJECT}"))
    except (ValueError, TypeError):
        return False


def build_pending_query(after: bool = False) -> str:
    cursor_clause = "AND s.parcel_id > %s" if after else ""
    return f"""
        SELECT s.parcel_id
        FROM god_mode_ops.lee_parcel_qualification_screening s
        LEFT JOIN god_mode_ops.lee_parcel_geometry_evidence g
          ON g.parcel_id = s.parcel_id
        WHERE s.county = 'Lee County'
          AND s.qualification_status = 'PENDING_SCREENING'
          AND g.parcel_id IS NULL
          {cursor_clause}
        ORDER BY s.parcel_id
        LIMIT %s
    """


def _validate_geojson(geometry: dict) -> None:
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise GeometryError("Source did not return a polygon")
    if len(json.dumps(geometry)) > 750_000:
        raise GeometryError("Source polygon exceeds safe size limit")
    # Shapely is used only for shape validation, not as evidence for legal access.
    from shapely.geometry import shape
    try:
        geom = shape(geometry)
    except (TypeError, ValueError, KeyError) as exc:
        raise GeometryError("Source geometry could not be parsed") from exc
    if geom.is_empty or not geom.is_valid or geom.area <= 0:
        raise GeometryError("Source returned invalid or zero-area polygon")
    west, south, east, north = geom.bounds
    # Broad sanity check: Lee County, Florida, WGS84 longitude/latitude.
    if not (-83 <= west < east <= -80 and 25 <= south < north <= 28):
        raise GeometryError("Polygon coordinates not within expected southwest Florida bounds")


def fetch_lee_geometry(session: requests.Session, parcel_id: str, timeout: float = 20.0):
    """Return source-backed polygon evidence, or None for a true unmatched STRAP."""
    strap = normalize_strap(parcel_id)
    params = {
        "where": f"STRAP='{strap}'",
        "outFields": "STRAP,OBJECTID",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    response = session.get(GIS_LAYER + "/query", params=params, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or "error" in result:
        raise GeometryError("Lee County GIS query error")
    features = result.get("features")
    if not isinstance(features, list):
        raise GeometryError("Missing GeoJSON feature list")
    if not features:
        return None
    if len(features) != 1 or result.get("exceededTransferLimit"):
        raise GeometryError("Ambiguous parcel query; expected exactly one geometry")
    feature = features[0]
    props = feature.get("properties") or {}
    if props.get("STRAP") != strap:
        raise GeometryError("Returned STRAP does not match requested parcel")
    objectid = props.get("OBJECTID", feature.get("id"))
    if not isinstance(objectid, int) or isinstance(objectid, bool) or objectid < 1:
        raise GeometryError("Missing valid source OBJECTID")
    geometry = feature.get("geometry")
    _validate_geojson(geometry)
    return {
        "parcel_id": parcel_id,
        "source_strap": strap,
        "source_objectid": objectid,
        "polygon_geojson": geometry,
        "source_url": response.url,
        "legally_verified_boundary": False,
    }


def build_session() -> requests.Session:
    retry = Retry(total=3, connect=3, read=2, backoff_factor=0.6,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    session = requests.Session()
    session.headers.update({"User-Agent": "GodMode-LeeParcelGeometry/1.0 (county GIS evidence acquisition)"})
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def open_staging_connection(dsn: str):
    if not staging_dsn_is_safe(dsn):
        raise ValueError("STAGING_SUPABASE_DB_URL must identify staging project " + STAGING_PROJECT)
    import psycopg2  # Database dependency is not needed for public dry-run --parcel-id.
    return psycopg2.connect(
        dsn, connect_timeout=12,
        sslmode="verify-full" if os.getenv("PGSSLROOTCERT") else "require",
    )


def store_geometry(conn, rows) -> int:
    """Only write source-backed polygons; update only the geometry job's progress."""
    if not rows:
        return 0
    from psycopg2.extras import Json, execute_values

    values = [(
        row["parcel_id"], row["source_strap"], row["source_objectid"],
        Json(row["polygon_geojson"]), row["source_url"],
    ) for row in rows]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO god_mode_ops.lee_parcel_geometry_evidence
              (parcel_id, source_strap, source_objectid, polygon_geojson,
               source_url, source_checked_at, evidence_type, legally_verified_boundary)
            VALUES %s
            ON CONFLICT (parcel_id) DO UPDATE
            SET source_strap = EXCLUDED.source_strap,
                source_objectid = EXCLUDED.source_objectid,
                polygon_geojson = EXCLUDED.polygon_geojson,
                source_url = EXCLUDED.source_url,
                source_checked_at = now(),
                evidence_type = 'GIS_PARCEL_POLYGON_ONLY',
                legally_verified_boundary = false
        """, values, template="(%s,%s,%s,%s,%s,now(),'GIS_PARCEL_POLYGON_ONLY',false)")
        cur.execute("""
            SELECT COUNT(*)::int FROM god_mode_ops.lee_parcel_qualification_screening
            WHERE county='Lee County'
        """)
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*)::int FROM god_mode_ops.lee_parcel_geometry_evidence g
            JOIN god_mode_ops.lee_parcel_qualification_screening s
              ON s.parcel_id=g.parcel_id
            WHERE s.county='Lee County'
        """)
        processed = cur.fetchone()[0]
        status = 'GEOMETRY_ACQUISITION_COMPLETE' if total and processed == total else 'PARTIAL_GEOMETRY_VERIFIED'
        cur.execute("""
            INSERT INTO god_mode_ops.lee_parcel_api_jobs
              (source_name, endpoint_url, total_parcels_queued, parcels_processed,
               job_status, endpoint_verified, endpoint_checked_at,
               verification_source_url, updated_at)
            VALUES ('LEE_PARCEL_GEOMETRY', %s, %s, %s, %s, true, now(), %s, now())
            ON CONFLICT (source_name) DO UPDATE
            SET endpoint_url=EXCLUDED.endpoint_url,
                total_parcels_queued=EXCLUDED.total_parcels_queued,
                parcels_processed=EXCLUDED.parcels_processed,
                job_status=EXCLUDED.job_status,
                endpoint_verified=true,
                endpoint_checked_at=now(),
                verification_source_url=EXCLUDED.verification_source_url,
                updated_at=now()
        """, (GIS_LAYER, total, processed, status, rows[-1]["source_url"]))
    conn.commit()
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25, help="1-1000 pending parcels per invocation (default 25)")
    parser.add_argument("--parcel-id", help="Test one named parcel without needing a DB connection in dry run")
    parser.add_argument("--write", action="store_true", help="Explicitly permit geometry-only staging writes")
    parser.add_argument("--all", action="store_true", help="Iterate the entire pending queue with cursor pagination")
    parser.add_argument("--delay", type=float, default=0.2, help="Request spacing in seconds")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 1000 or args.delay < 0:
        parser.error("--limit must be 1..1000 and --delay >= 0")

    dsn = os.getenv("STAGING_SUPABASE_DB_URL", "")
    conn = None
    if args.write or not args.parcel_id:
        if not dsn:
            print("BLOCKED: set STAGING_SUPABASE_DB_URL for staging queue access", file=sys.stderr)
            return 2
        try:
            conn = open_staging_connection(dsn)
        except Exception as exc:
            print(f"BLOCKED: staging database connection failed ({type(exc).__name__})", file=sys.stderr)
            return 2
    try:
        total_validated = total_not_found = total_errors = 0
        cursor = ""
        with build_session() as session:
            while True:
                if args.parcel_id:
                    parcel_ids = [args.parcel_id] if not cursor else []
                else:
                    with conn.cursor() as cur:
                        params = (cursor, args.limit) if cursor else (args.limit,)
                        cur.execute(build_pending_query(after=bool(cursor)), params)
                        parcel_ids = [row[0] for row in cur.fetchall()]
                    conn.rollback()  # End DB read transaction before HTTP calls.
                if not parcel_ids:
                    break
                cursor = parcel_ids[-1]  # Keyset cursor also advances past unmatched STRAPs.
                successes, not_found, errors = [], 0, 0
                for parcel_id in parcel_ids:
                    try:
                        evidence = fetch_lee_geometry(session, parcel_id)
                        if evidence is None:
                            not_found += 1
                        else:
                            successes.append(evidence)
                    except (requests.RequestException, GeometryError, ValueError) as exc:
                        errors += 1
                        print(f"SKIP {parcel_id}: {type(exc).__name__}")
                    if args.delay:
                        time.sleep(args.delay)
                if args.write and successes:
                    try:
                        saved = store_geometry(conn, successes)
                    except Exception:
                        conn.rollback()
                        raise
                    print(f"STAGING GEOMETRY ONLY: saved batch={saved} no_match={not_found} errors={errors}")
                else:
                    print(f"DRY RUN (NO WRITES): valid={len(successes)} no_match={not_found} errors={errors}")
                total_validated += len(successes)
                total_not_found += not_found
                total_errors += errors
                if not args.all or args.parcel_id:
                    break
        print(f"SUMMARY: validated={total_validated} not_found={total_not_found} errors={total_errors}")
        print("Flood, wetlands, current tax, ownership, legal access, and qualifications remain UNVERIFIED.")
        return 1 if total_errors or total_not_found else 0
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # No credentials, GIS payloads, or SQL details in logs.
        print(f"WORKER BLOCKED: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
