-- Lee screening geometry quarantine. No actual survey, flood, ownership, tax or access is asserted.
-- Database change already verified in staging project ltwqgkcblvtscgldxvjw.
CREATE OR REPLACE FUNCTION god_mode_ops.mark_lee_geometry_reconciled()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog, god_mode_ops
AS $$
BEGIN
  UPDATE god_mode_ops.lee_parcel_geometry_reconciliation
     SET status='RESOLVED_WITH_NEW_SOURCE',
         reconciliation_notes='A source-backed polygon is now available; legal boundary and seller qualification remain unverified.',
         updated_at=now()
   WHERE parcel_id=NEW.parcel_id AND status<>'RESOLVED_WITH_NEW_SOURCE';
  UPDATE god_mode_ops.lee_parcel_qualification_screening
     SET qualification_status='PENDING_SCREENING'
   WHERE parcel_id=NEW.parcel_id
     AND qualification_status='GEOMETRY_RECONCILIATION_REQUIRED';
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION god_mode_ops.mark_lee_geometry_reconciled() FROM PUBLIC, anon, authenticated;
UPDATE god_mode_ops.lee_parcel_qualification_screening s
SET qualification_status='GEOMETRY_RECONCILIATION_REQUIRED'
FROM god_mode_ops.lee_parcel_geometry_reconciliation r
WHERE r.parcel_id=s.parcel_id AND r.status='GEOMETRY_UNRESOLVED'
  AND s.qualification_status='PENDING_SCREENING'
  AND NOT EXISTS(
    SELECT 1 FROM god_mode_ops.lee_parcel_geometry_evidence g WHERE g.parcel_id=s.parcel_id
  );
DO $$
BEGIN
 IF EXISTS(
  SELECT 1 FROM god_mode_ops.lee_parcel_geometry_reconciliation r
  JOIN god_mode_ops.lee_parcel_qualification_screening s USING(parcel_id)
  WHERE r.status='GEOMETRY_UNRESOLVED'
    AND s.qualification_status<>'GEOMETRY_RECONCILIATION_REQUIRED'
 ) THEN RAISE EXCEPTION 'Unresolved geometry queue and screening status disagree'; END IF;
 IF EXISTS(
  SELECT 1 FROM god_mode_ops.lee_parcel_qualification_screening s
  JOIN god_mode_ops.lee_parcel_geometry_evidence g USING(parcel_id)
  WHERE s.qualification_status='GEOMETRY_RECONCILIATION_REQUIRED'
 ) THEN RAISE EXCEPTION 'Geometry hold has source-backed polygon';END IF;
END $$;
