-- FEMA county NFHL static archive evidence in private Supabase staging.
-- Install PostGIS in extensions schema, never public.
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;
CREATE TABLE IF NOT EXISTS god_mode_ops.fema_nfhl_static_datasets (
 dataset_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 product_id text NOT NULL CHECK (product_id IN ('NFHL_12071C','NFHL_12')),
 source_url text NOT NULL CHECK (source_url LIKE 'https://msc.fema.gov/portal/downloadProduct?productID=NFHL_%'),
 archive_sha256 text NOT NULL CHECK (archive_sha256 ~ '^[a-f0-9]{64}$'),
 retrieved_at timestamptz NOT NULL DEFAULT now(),
 layer_name text NOT NULL DEFAULT 'S_Fld_Haz_Ar',
 load_status text NOT NULL DEFAULT 'LOADING'
   CHECK (load_status IN ('LOADING','VALIDATED','BLOCKED')),
 feature_count integer NOT NULL DEFAULT 0 CHECK (feature_count>=0),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(product_id,archive_sha256)
);
ALTER TABLE god_mode_ops.fema_nfhl_static_datasets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.fema_nfhl_static_datasets FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS god_mode_ops.fema_nfhl_hazard_polygons (
 dataset_id uuid NOT NULL REFERENCES god_mode_ops.fema_nfhl_static_datasets(dataset_id),
 source_fld_ar_id text NOT NULL,
 fld_zone text NOT NULL CHECK (length(btrim(fld_zone))>0),
 zone_subty text,
 sfha_tf text,
 geom extensions.geometry(MultiPolygon,4326) NOT NULL,
 PRIMARY KEY (dataset_id,source_fld_ar_id),
 CHECK (extensions.ST_IsValid(geom) AND NOT extensions.ST_IsEmpty(geom))
);
ALTER TABLE god_mode_ops.fema_nfhl_hazard_polygons ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.fema_nfhl_hazard_polygons FROM PUBLIC,anon,authenticated;
CREATE INDEX IF NOT EXISTS fema_nfhl_hazard_polygons_spatial_gix
 ON god_mode_ops.fema_nfhl_hazard_polygons USING GIST(geom);
CREATE INDEX IF NOT EXISTS fema_nfhl_hazard_polygons_dataset_idx
 ON god_mode_ops.fema_nfhl_hazard_polygons(dataset_id);

CREATE TABLE IF NOT EXISTS god_mode_ops.lee_parcel_geometry_postgis (
 parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_geometry_evidence(parcel_id),
 source_checked_at timestamptz NOT NULL,
 geom extensions.geometry(MultiPolygon,4326) NOT NULL,
 CHECK (extensions.ST_IsValid(geom) AND NOT extensions.ST_IsEmpty(geom))
);
ALTER TABLE god_mode_ops.lee_parcel_geometry_postgis ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.lee_parcel_geometry_postgis FROM PUBLIC,anon,authenticated;
CREATE INDEX IF NOT EXISTS lee_parcel_geometry_postgis_spatial_gix
 ON god_mode_ops.lee_parcel_geometry_postgis USING GIST(geom);

INSERT INTO god_mode_ops.lee_parcel_geometry_postgis(parcel_id,source_checked_at,geom)
 SELECT parcel_id,source_checked_at,
    extensions.ST_Multi(extensions.ST_SetSRID(extensions.ST_GeomFromGeoJSON(polygon_geojson::text),4326))
 FROM god_mode_ops.lee_parcel_geometry_evidence
 WHERE source_url LIKE 'https://gismapserver.leegov.com/%'
ON CONFLICT (parcel_id) DO UPDATE
 SET source_checked_at=EXCLUDED.source_checked_at,geom=EXCLUDED.geom;

ALTER TABLE god_mode_ops.lee_parcel_fema_intersections
 DROP CONSTRAINT IF EXISTS lee_parcel_fema_intersections_source_url_check;
ALTER TABLE god_mode_ops.lee_parcel_fema_intersections
 ADD CONSTRAINT lee_parcel_fema_intersections_source_url_check
 CHECK (source_url LIKE 'https://hazards.fema.gov/%'
    OR source_url LIKE 'https://msc.fema.gov/portal/downloadProduct?productID=NFHL_%');
ALTER TABLE god_mode_ops.lee_parcel_fema_intersections
 ADD COLUMN IF NOT EXISTS local_dataset_id uuid REFERENCES god_mode_ops.fema_nfhl_static_datasets(dataset_id),
 ADD COLUMN IF NOT EXISTS fema_coverage_pct numeric(7,4)
 CHECK (fema_coverage_pct IS NULL OR fema_coverage_pct BETWEEN 0 AND 100);
