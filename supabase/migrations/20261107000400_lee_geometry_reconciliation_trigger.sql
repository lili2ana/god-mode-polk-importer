-- Resolution is only a geometry-source event. It is NOT legal-boundary verification or lead approval.
CREATE OR REPLACE FUNCTION god_mode_ops.mark_lee_geometry_reconciled()
RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog, god_mode_ops
AS $$
BEGIN
  UPDATE god_mode_ops.lee_parcel_geometry_reconciliation
  SET status='RESOLVED_WITH_NEW_SOURCE',
      reconciliation_notes='A matching source-backed county polygon is now in lee_parcel_geometry_evidence; legal boundary and seller qualification remain unverified.',
      updated_at=now()
  WHERE parcel_id=NEW.parcel_id AND status<>'RESOLVED_WITH_NEW_SOURCE';
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION god_mode_ops.mark_lee_geometry_reconciled() FROM PUBLIC, anon, authenticated;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgrelid='god_mode_ops.lee_parcel_geometry_evidence'::regclass
      AND tgname='trg_lee_geometry_reconciled' AND NOT tgisinternal
  ) THEN
    CREATE TRIGGER trg_lee_geometry_reconciled
    AFTER INSERT OR UPDATE OF polygon_geojson, source_strap
    ON god_mode_ops.lee_parcel_geometry_evidence
    FOR EACH ROW EXECUTE FUNCTION god_mode_ops.mark_lee_geometry_reconciled();
  END IF;
END $$;
