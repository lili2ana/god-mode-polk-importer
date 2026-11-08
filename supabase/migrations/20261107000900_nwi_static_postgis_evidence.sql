-- USFWS NWI Florida October 2026-style GeoPackage snapshot ingestion: data is always source-tracked.
-- All tables stay in private god_mode_ops; not exposed to anon/authenticated roles.
CREATE TABLE IF NOT EXISTS god_mode_ops.nwi_static_datasets (
  dataset_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url text NOT NULL CHECK (source_url='https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/FL_geopackage_wetlands.zip'),
  archive_sha256 text NOT NULL CHECK (archive_sha256 ~ '^[a-f0-9]{64}$'),
  spatial_scope text NOT NULL DEFAULT 'LEE_QUEUE_BBOX' CHECK (spatial_scope='LEE_QUEUE_BBOX'),
  load_status text NOT NULL DEFAULT 'LOADING' CHECK (load_status IN ('LOADING','VALIDATED','BLOCKED')),
  feature_count integer NOT NULL DEFAULT 0 CHECK (feature_count>=0),
  retrieved_at timestamptz NOT NULL DEFAULT now(),
  validated_at timestamptz,
  UNIQUE(source_url, archive_sha256),
  CHECK (load_status<>'VALIDATED' OR (feature_count>0 AND validated_at IS NOT NULL))
);
ALTER TABLE god_mode_ops.nwi_static_datasets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.nwi_static_datasets FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS god_mode_ops.nwi_wetlands_polygons (
  dataset_id uuid NOT NULL REFERENCES god_mode_ops.nwi_static_datasets(dataset_id),
  feature_hash text NOT NULL CHECK (feature_hash ~ '^[a-f0-9]{64}$'),
  attribute_code text NOT NULL,
  wetland_type text NOT NULL CHECK (length(btrim(wetland_type))>0),
  feature_class text NOT NULL CHECK (feature_class IN ('WETLAND','AQUATIC_OTHER')),
  geom extensions.geometry(MultiPolygon,4326) NOT NULL,
  PRIMARY KEY(dataset_id,feature_hash),
  CHECK(extensions.ST_IsValid(geom) AND NOT extensions.ST_IsEmpty(geom))
);
ALTER TABLE god_mode_ops.nwi_wetlands_polygons ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.nwi_wetlands_polygons FROM PUBLIC,anon,authenticated;
CREATE INDEX IF NOT EXISTS nwi_polygons_gist ON god_mode_ops.nwi_wetlands_polygons USING gist(geom);
CREATE INDEX IF NOT EXISTS nwi_polygons_by_dataset ON god_mode_ops.nwi_wetlands_polygons(dataset_id);

CREATE TABLE IF NOT EXISTS god_mode_ops.lee_parcel_nwi_intersections (
  parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_geometry_evidence(parcel_id),
  dataset_id uuid NOT NULL REFERENCES god_mode_ops.nwi_static_datasets(dataset_id),
  nwi_wetland_pct numeric(7,4) NOT NULL CHECK(nwi_wetland_pct BETWEEN 0 AND 100),
  nwi_mapped_coverage_pct numeric(7,4) NOT NULL CHECK(nwi_mapped_coverage_pct BETWEEN 0 AND 100),
  matched_wetland_features integer NOT NULL CHECK(matched_wetland_features>=0),
  matched_total_features integer NOT NULL CHECK(matched_total_features>=matched_wetland_features),
  review_status text NOT NULL CHECK(review_status='REVIEW_REQUIRED'),
  source_url text NOT NULL CHECK(source_url='https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/FL_geopackage_wetlands.zip'),
  computed_at timestamptz NOT NULL DEFAULT now(),
  CHECK(nwi_wetland_pct<=nwi_mapped_coverage_pct)
);
ALTER TABLE god_mode_ops.lee_parcel_nwi_intersections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.lee_parcel_nwi_intersections FROM PUBLIC,anon,authenticated;
CREATE INDEX IF NOT EXISTS lee_parcel_nwi_dataset_idx ON god_mode_ops.lee_parcel_nwi_intersections(dataset_id);