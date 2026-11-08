-- Staging source-backed Lee County parcel polygons. Not FEMA, NWI, tax or legal access evidence.
-- This schema also exists in staging as of 2026-11-07; the migration is idempotent.
CREATE SCHEMA IF NOT EXISTS god_mode_ops;
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_parcel_geometry_evidence (
    parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_qualification_screening(parcel_id),
    source_strap text NOT NULL,
    source_objectid bigint NOT NULL,
    polygon_geojson jsonb NOT NULL,
    source_url text NOT NULL,
    source_checked_at timestamptz NOT NULL DEFAULT now(),
    evidence_type text NOT NULL DEFAULT 'GIS_PARCEL_POLYGON_ONLY'
        CHECK (evidence_type = 'GIS_PARCEL_POLYGON_ONLY'),
    legally_verified_boundary boolean NOT NULL DEFAULT false
        CHECK (legally_verified_boundary = false),
    CONSTRAINT lee_geojson_polygon_shape_chk CHECK (
        polygon_geojson->>'type' IN ('Polygon', 'MultiPolygon')
        AND jsonb_typeof(polygon_geojson->'coordinates') = 'array'
    ),
    CONSTRAINT lee_geometry_source_strap_unique UNIQUE (source_strap)
);
ALTER TABLE god_mode_ops.lee_parcel_geometry_evidence ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.lee_parcel_geometry_evidence FROM PUBLIC, anon, authenticated;
