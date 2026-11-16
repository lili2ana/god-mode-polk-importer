
-- Lee master screening regression checks. Read-only, fail-closed.
DO $$
DECLARE total integer;
DECLARE queued integer;
DECLARE invalid_releases integer;
DECLARE geometry_mismatches integer;
DECLARE leaked_holds integer;
BEGIN
 SELECT COUNT(*) INTO total FROM god_mode_ops.lee_master_screening;
 SELECT COUNT(*) INTO queued FROM god_mode_ops.lee_parcel_qualification_screening WHERE county='Lee County';
 IF total<>queued THEN
  RAISE EXCEPTION 'Master rows % do not reconcile to Lee screening queue %',total,queued;
 END IF;
 SELECT COUNT(*) INTO invalid_releases
 FROM god_mode_ops.lee_master_screening
 WHERE eligible_for_contact
   AND (NOT geometry_verified OR NOT fema_verified OR NOT nwi_verified
     OR NOT current_tax_verified OR NOT current_delinquency_verified
     OR NOT owner_value_verified OR NOT title_access_verified
     OR evidence_screening_status<>'QUALIFIED_FOR_CONTACT_ENRICHMENT'
     OR COALESCE(fema_flood_category,'')<>'Low (X-Unshaded)'
     OR COALESCE(sfha_overlap_pct,-1)<>0
     OR COALESCE(nwi_wetland_pct,-1)<>0);
 IF invalid_releases<>0 THEN
  RAISE EXCEPTION 'Unverified seller leads released: %',invalid_releases;
 END IF;
 SELECT COUNT(*) INTO geometry_mismatches
 FROM god_mode_ops.lee_master_screening m
 LEFT JOIN god_mode_ops.lee_parcel_geometry_evidence g ON g.parcel_id=m.parcel_id
 WHERE (g.parcel_id IS NULL AND m.geometry_verified)
    OR (g.parcel_id IS NOT NULL AND NOT m.geometry_verified);
 IF geometry_mismatches<>0 THEN
  RAISE EXCEPTION 'Master geometry mismatch: %',geometry_mismatches;
 END IF;
 SELECT COUNT(*) INTO leaked_holds
 FROM god_mode_ops.lee_master_screening m
 JOIN god_mode_ops.lee_parcel_geometry_reconciliation r ON r.parcel_id=m.parcel_id
 WHERE r.status='GEOMETRY_UNRESOLVED'
   AND (m.evidence_screening_status<>'HOLD_GEOMETRY_RECONCILIATION'
        OR m.eligible_for_contact);
 IF leaked_holds<>0 THEN
  RAISE EXCEPTION 'Reconciliation holds missing: %',leaked_holds;
 END IF;
END $$;
SELECT evidence_screening_status,COUNT(*) AS parcels,
       COUNT(*) FILTER (WHERE eligible_for_contact) AS eligible
FROM god_mode_ops.lee_master_screening
GROUP BY evidence_screening_status
ORDER BY evidence_screening_status;
