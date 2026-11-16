#!/usr/bin/env python3
"""Verify parcel-specific FEMA NFHL intersections from real Lee County GIS polygons.

Fail-closed: no FEMA API response means no flood-zone evidence and no job completion.
Never changes tax, wetlands, legal-access, ownership, or qualification fields.
Requires a staging-only database DSN for database access.
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

import requests
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from pyproj import Transformer

from run_lee_parcel_geometry_worker import (build_session, open_staging_connection,
                                            staging_dsn_is_safe)

# Official FEMA National Flood Hazard Layer service, Flood Hazard Zones (layer 28).
FEMA_LAYER = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
FEMA_FIELDS = "FLD_ZONE,ZONE_SUBTY,SFHA_TF,FLD_AR_ID"
TO_METERS = Transformer.from_crs("EPSG:4326", "EPSG:26917", always_xy=True)


class FEMAError(ValueError):
    """FEMA source evidence absent, ambiguous or invalid."""


def validate_nfhl_layer(meta: dict) -> None:
    if not isinstance(meta, dict) or meta.get("error"):
        raise FEMAError("FEMA metadata query failed")
    name = str(meta.get("name", "")).lower()
    if not any(token in name for token in ("flood hazard zones", "s_fld_haz_ar")):
        raise FEMAError("Unexpected NFHL layer name")
    if meta.get("geometryType") not in ("esriGeometryPolygon", None):
        raise FEMAError("FEMA layer is not polygons")
    fields = {x.get("name", "").upper() for x in meta.get("fields", []) if isinstance(x, dict)}
    if not {"FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"}.issubset(fields):
        raise FEMAError("FEMA layer lacks required hazard fields")


def fetch_nfhl_metadata(session) -> None:
    response = session.get(FEMA_LAYER, params={"f": "json"}, timeout=25)
    response.raise_for_status()
    validate_nfhl_layer(response.json())


def fema_category(zone: str, subtype: str) -> str:
    zone = (zone or "").strip().upper()
    st = (subtype or "").strip().upper()
    if zone in {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE", "A1-30", "V1-30"}:
        return "High Risk (A/V)"
    if zone in {"X", "B", "C"}:
        if "0.2" in st or "SHADED" in st or zone == "B":
            return "Moderate (X-Shaded)"
        return "Low (X-Unshaded)"
    if zone == "D":
        return "Undetermined (D)"
    if zone == "OPEN WATER":
        return "Open Water"
    raise FEMAError("Unrecognized FEMA flood zone (fail closed)")


def _checked_shape(geojson: dict):
    try:
        geom = shape(geojson)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FEMAError("Invalid polygon payload") from exc
    if geom.geom_type not in ("Polygon", "MultiPolygon") or geom.is_empty or not geom.is_valid:
        raise FEMAError("Invalid or nonpolygon parcel geometry")
    return geom


def build_fema_query(parcel: dict) -> dict:
    geom = _checked_shape(parcel)
    west, south, east, north = geom.bounds
    if not (-83 < west < east < -80 and 25 < south < north < 28):
        raise FEMAError("Lee County parcel outside expected coordinates")
    envelope = {"xmin": west, "ymin": south, "xmax": east, "ymax": north,
                "spatialReference": {"wkid": 4326}}
    return {
        "geometry": json.dumps(envelope, separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326", "outSR": "4326", "f": "geojson",
        "outFields": FEMA_FIELDS, "returnGeometry": "true",
        "where": "1=1", "resultRecordCount": "2000",
    }


def get_fema_features(session, parcel: dict) -> list:
    params = build_fema_query(parcel)
    response = session.post(FEMA_LAYER + "/query", data=params, timeout=45)
    response.raise_for_status()
    obj = response.json()
    if not isinstance(obj, dict) or obj.get("error") or obj.get("exceededTransferLimit"):
        raise FEMAError("FEMA NFHL query failed or was truncated")
    if obj.get("type") != "FeatureCollection" or not isinstance(obj.get("features"), list):
        raise FEMAError("FEMA returned a non-GeoJSON or unexpected response")
    if len(obj["features"]) >= 2000:
        raise FEMAError("Potential FEMA NFHL record truncation")
    return obj["features"]


def interpret_nfhl_features(parcel: dict, features: list) -> dict:
    source_parcel = _checked_shape(parcel)
    parcel_m = transform(TO_METERS.transform, source_parcel)
    parcel_area = parcel_m.area
    if parcel_area <= 0:
        raise FEMAError("Zero-area parcel")
    per_zone = defaultdict(list)
    sfha_parts = []
    validated_features = 0
    for f in features:
        props = f.get("properties") or {}
        geom_data = f.get("geometry")
        if not geom_data:
            continue
        src = _checked_shape(geom_data)
        intersection = parcel_m.intersection(transform(TO_METERS.transform, src))
        if intersection.is_empty or intersection.area <= 0:
            continue
        zone = (props.get("FLD_ZONE") or "").strip().upper()
        subtype = props.get("ZONE_SUBTY") or ""
        category = fema_category(zone, subtype)
        if props.get("SFHA_TF") == "T" and category != "High Risk (A/V)":
            raise FEMAError("FEMA SFHA flag conflicts with classification")
        key = (zone, category)
        per_zone[key].append(intersection)
        if category == "High Risk (A/V)":
            sfha_parts.append(intersection)
        validated_features += 1
    if not per_zone:
        raise FEMAError("No verified FEMA polygon overlaps parcel; leave flood zone unknown")
    zone_areas = [(k, unary_union(parts).area) for k, parts in per_zone.items()]
    # Conservative: any material SFHA coverage triggers high-risk review.
    high = [(k, area) for k, area in zone_areas if k[1] == "High Risk (A/V)"]
    selected = sorted(high if high else zone_areas, key=lambda x: x[1], reverse=True)[0][0]
    sfha = unary_union(sfha_parts).area if sfha_parts else 0.0
    sfha_pct = min(100.0, max(0.0, sfha / parcel_area * 100.0))
    return {
        "fema_flood_zone": selected[0],
        "fema_flood_category": selected[1],
        "sfha_overlap_pct": round(sfha_pct, 4),
        "mixed_zones": len(per_zone) > 1,
        "review_status": "REVIEW_REQUIRED" if high or len(per_zone) > 1 or selected[1] == "Undetermined (D)" else "EVIDENCE_CAPTURED",
        "overlap_breakdown": [
            {"flood_zone": k[0], "category": k[1],
             "parcel_overlap_pct": round(min(100.0, area / parcel_area * 100), 4)}
            for k, area in sorted(zone_areas, key=lambda x: x[1], reverse=True)
        ],
        "source_feature_count": validated_features,
    }


def read_pending(conn, limit: int, after: str = "") -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT g.parcel_id, g.polygon_geojson
            FROM god_mode_ops.lee_parcel_geometry_evidence g
            JOIN god_mode_ops.lee_parcel_qualification_screening s ON s.parcel_id=g.parcel_id
            LEFT JOIN god_mode_ops.lee_parcel_fema_intersections f ON f.parcel_id=g.parcel_id
            WHERE s.county='Lee County' AND s.qualification_status='PENDING_SCREENING'
              AND f.parcel_id IS NULL AND g.parcel_id > %s
            ORDER BY g.parcel_id LIMIT %s
        """, (after, limit))
        rows = cur.fetchall()
    conn.rollback()
    return rows


def persist_nfhl(conn, records: list) -> int:
    if not records:
        return 0
    from psycopg2.extras import execute_values, Json
    rows = [(x["parcel_id"], x["fema_flood_zone"], x["fema_flood_category"],
             x["sfha_overlap_pct"], x["mixed_zones"], x["review_status"],
             Json(x["overlap_breakdown"]), x["source_feature_count"], FEMA_LAYER + "/query")
            for x in records]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO god_mode_ops.lee_parcel_fema_intersections
            (parcel_id, fema_flood_zone, fema_flood_category, sfha_overlap_pct,
             mixed_zones, review_status, overlap_breakdown, source_feature_count,
             source_url, source_checked_at)
            VALUES %s ON CONFLICT (parcel_id) DO NOTHING
        """, rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,now())")
        cur.execute("""UPDATE god_mode_ops.lee_parcel_api_jobs
            SET parcels_processed=(SELECT COUNT(*) FROM god_mode_ops.lee_parcel_fema_intersections),
                job_status='PARTIAL_FEMA_EVIDENCE_VERIFIED', updated_at=now()
            WHERE source_name='FEMA_NFHL'""")
    conn.commit()
    return len(records)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200 or args.delay < 0:
        parser.error("--limit must be 1..200, --delay >=0")
    dsn = os.getenv("STAGING_SUPABASE_DB_URL", "")
    if not staging_dsn_is_safe(dsn):
        print("BLOCKED: staging-only STAGING_SUPABASE_DB_URL required", file=sys.stderr)
        return 2
    with build_session() as session:
        try:
            fetch_nfhl_metadata(session)
        except (requests.RequestException, FEMAError, ValueError) as err:
            print("FEMA NFHL ENDPOINT BLOCKED (no data written):", type(err).__name__)
            return 3
        conn = open_staging_connection(dsn)
        try:
            rows = read_pending(conn, args.limit)
            good, failed = [], 0
            for parcel_id, geo in rows:
                try:
                    features = get_fema_features(session, geo)
                    result = interpret_nfhl_features(geo, features)
                    good.append({**result, "parcel_id": parcel_id})
                except (requests.RequestException, FEMAError, ValueError):
                    failed += 1
                    print("FEMA EVIDENCE MISSING:", parcel_id)
                if args.delay:
                    time.sleep(args.delay)
            if args.write:
                count = persist_nfhl(conn, good)
                print("Verified FEMA records saved:", count)
            else:
                print("DRY RUN: validated FEMA spatial overlaps:", len(good))
            print("Not verified:", failed)
            print("Tax, wetlands, legal access and lead qualifications remain unchanged.")
            return 1 if failed else 0
        finally:
            conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("FEMA worker blocked:", type(e).__name__, file=sys.stderr)
        sys.exit(1)
