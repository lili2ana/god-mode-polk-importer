-- Read-only regression check. Run with the staging database service role.
-- A source-backed geometry must not be quarantined as unresolved; all missing geometry must be queued.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM god_mode_ops.lee_parcel_geometry_reconciliation r
    JOIN god_mode_ops.lee_parcel_geometry_evidence g ON g.parcel_id=r.parcel_id
    WHERE r.status='GEOMETRY_UNRESOLVED'
  ) THEN RAISE EXCEPTION 'Geometry evidence and unresolved status conflict'; END IF;
  IF EXISTS (
    SELECT 1 FROM god_mode_ops.lee_parcel_qualification_screening s
    LEFT JOIN god_mode_ops.lee_parcel_geometry_evidence g ON g.parcel_id=s.parcel_id
    LEFT JOIN god_mode_ops.lee_parcel_geometry_reconciliation r ON r.parcel_id=s.parcel_id
    WHERE s.county='Lee County' AND g.parcel_id IS NULL AND r.parcel_id IS NULL
  ) THEN RAISE EXCEPTION 'Missing Lee parcel without reconciliation entry'; END IF;
END $$;
SELECT
  (SELECT COUNT(*) FROM god_mode_ops.lee_parcel_geometry_evidence) AS source_backed_geometry,
  (SELECT COUNT(*) FROM god_mode_ops.lee_parcel_geometry_reconciliation
   WHERE status='GEOMETRY_UNRESOLVED') AS unresolved_quarantine,
  (SELECT COUNT(*) FROM god_mode_ops.lee_parcel_fema_intersections) AS verified_fema_intersections;
