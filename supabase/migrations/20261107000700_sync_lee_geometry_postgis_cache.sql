-- Ensure the private PostGIS parcel copy is kept in sync with source-backed Lee GIS.
CREATE OR REPLACE FUNCTION god_mode_ops.cache_verified_lee_postgis_polygon()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,god_mode_ops,extensions
AS $$
DECLARE spatial_shape extensions.geometry(MultiPolygon,4326);
BEGIN
 IF NEW.source_url NOT LIKE 'https://gismapserver.leegov.com/%' THEN
    RAISE EXCEPTION 'Parcel geometry source not official Lee County GIS';
 END IF;
 spatial_shape := extensions.ST_Multi(
    extensions.ST_SetSRID(
      extensions.ST_GeomFromGeoJSON(NEW.polygon_geojson::text),4326
    )
 );
 IF spatial_shape IS NULL OR extensions.ST_IsEmpty(spatial_shape) OR
    NOT extensions.ST_IsValid(spatial_shape) THEN
    RAISE EXCEPTION 'Invalid GIS polygon cannot enter PostGIS cache';
 END IF;
 INSERT INTO god_mode_ops.lee_parcel_geometry_postgis(parcel_id,source_checked_at,geom)
 VALUES(NEW.parcel_id,NEW.source_checked_at,spatial_shape)
 ON CONFLICT(parcel_id) DO UPDATE SET source_checked_at=EXCLUDED.source_checked_at,
   geom=EXCLUDED.geom;
 RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION god_mode_ops.cache_verified_lee_postgis_polygon() FROM PUBLIC,anon,authenticated;
DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE
 tgrelid='god_mode_ops.lee_parcel_geometry_evidence'::regclass
 AND tgname='trg_cache_verified_lee_postgis' AND NOT tgisinternal) THEN
   CREATE TRIGGER trg_cache_verified_lee_postgis
    AFTER INSERT OR UPDATE OF polygon_geojson,source_strap,source_checked_at
    ON god_mode_ops.lee_parcel_geometry_evidence
    FOR EACH ROW EXECUTE FUNCTION god_mode_ops.cache_verified_lee_postgis_polygon();
 END IF;
END $$;