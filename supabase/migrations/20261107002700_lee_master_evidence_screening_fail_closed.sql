
-- Lee County God Mode: fail-closed evidence unification and screening.
-- Never promotes an applicant from static geometry, stale 2024 tax snapshots,
-- raw tax imports, unvalidated environmental datasets, or unchecked titles.
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_verified_owner_valuation (
  parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_qualification_screening(parcel_id),
  owner_name text NOT NULL CHECK (length(btrim(owner_name)) > 0),
  current_site_address text NOT NULL CHECK (length(btrim(current_site_address)) > 0),
  appraiser_just_value_usd numeric(14,2) NOT NULL CHECK (appraiser_just_value_usd > 0),
  valuation_year integer NOT NULL CHECK (valuation_year BETWEEN 2020 AND 2100),
  source_authority text NOT NULL CHECK (source_authority='LEE_PROPERTY_APPRAISER'),
  source_url text NOT NULL CHECK (source_url LIKE 'https://%'),
  source_checked_at timestamptz NOT NULL,
  verification_status text NOT NULL DEFAULT 'REVIEW_REQUIRED'
    CHECK (verification_status IN ('REVIEW_REQUIRED','VERIFIED_SOURCE','REJECTED_SOURCE')),
  verified_at timestamptz,
  CHECK (verification_status <> 'VERIFIED_SOURCE' OR verified_at IS NOT NULL)
);
ALTER TABLE god_mode_ops.lee_verified_owner_valuation ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.lee_verified_owner_valuation FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS god_mode_ops.lee_verified_title_access (
  parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_qualification_screening(parcel_id),
  recorded_title_cleared boolean NOT NULL DEFAULT false,
  recorded_legal_access_verified boolean NOT NULL DEFAULT false,
  paved_road_verified boolean NOT NULL DEFAULT false,
  source_url text NOT NULL CHECK (source_url LIKE 'https://%'),
  source_checked_at timestamptz NOT NULL,
  verification_status text NOT NULL DEFAULT 'REVIEW_REQUIRED'
    CHECK(verification_status IN ('REVIEW_REQUIRED','VERIFIED_SOURCE','REJECTED_SOURCE')),
  verified_at timestamptz,
  CHECK(verification_status <> 'VERIFIED_SOURCE' OR verified_at IS NOT NULL)
);
ALTER TABLE god_mode_ops.lee_verified_title_access ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.lee_verified_title_access FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE VIEW god_mode_ops.v_lee_master_screening_source
WITH (security_invoker=true)
AS
WITH intake AS (
  SELECT DISTINCT ON (parcel_id) parcel_id, raw_row->>5 AS historical_site_address
  FROM god_mode_ops.lee_tax_report_intake
  ORDER BY parcel_id,source_checked_at DESC,id DESC
),
tax AS (
  SELECT DISTINCT ON (o.parcel_id)
    o.parcel_id,o.tax_year,o.reported_status,o.delinquent_claimed,
    o.amount_due,b.report_generated_date,b.source_url,
    b.validation_status,b.archive_sha256
  FROM god_mode_ops.lee_tax_report_import_observations o
  JOIN god_mode_ops.lee_tax_report_import_batches b ON b.batch_id=o.batch_id
  WHERE b.validation_status='VERIFIED_SOURCE'
  ORDER BY o.parcel_id,b.report_generated_date DESC,o.imported_at DESC
),
base AS (
SELECT
  s.parcel_id,s.county,i.historical_site_address,
  g.source_url AS geometry_source_url,g.source_checked_at AS geometry_checked_at,
  (
    g.parcel_id IS NOT NULL AND g.source_url LIKE 'https://gismapserver.leegov.com/%'
    AND g.source_objectid>0
    AND g.polygon_geojson->>'type' IN ('Polygon','MultiPolygon')
  ) AS geometry_verified,
  f.fema_flood_zone,f.fema_flood_category,f.sfha_overlap_pct,
  f.fema_coverage_pct,f.source_url AS fema_source_url,
  (
    f.parcel_id IS NOT NULL AND f.local_dataset_id IS NOT NULL
    AND fd.load_status='VALIDATED' AND fd.feature_count>0
    AND fd.archive_sha256 ~ '^[a-f0-9]{64}$'
    AND f.source_url=fd.source_url
  ) AS fema_verified,
  (
    f.parcel_id IS NOT NULL AND f.local_dataset_id IS NOT NULL
    AND fd.load_status='VALIDATED' AND fd.feature_count>0
    AND f.source_url=fd.source_url
    AND f.fema_flood_category='Low (X-Unshaded)'
    AND f.review_status='EVIDENCE_CAPTURED'
    AND f.mixed_zones=false
    AND f.sfha_overlap_pct=0
    AND COALESCE(f.fema_coverage_pct,0)>=99
  ) AS fema_clear,
  n.nwi_wetland_pct,n.nwi_mapped_coverage_pct,n.source_url AS nwi_source_url,
  (
    n.parcel_id IS NOT NULL AND nd.load_status='VALIDATED'
    AND nd.feature_count>0 AND nd.validated_at IS NOT NULL
    AND nd.archive_sha256 ~ '^[a-f0-9]{64}$'
    AND n.source_url=nd.source_url
  ) AS nwi_verified,
  tax.tax_year AS verified_tax_year,tax.reported_status AS tax_reported_status,
  tax.amount_due AS tax_amount_due,tax.source_url AS tax_source_url,
  (
    tax.parcel_id IS NOT NULL
    AND tax.report_generated_date >= CURRENT_DATE-90
    AND tax.archive_sha256 ~ '^[a-f0-9]{64}$'
  ) AS current_tax_verified,
  (
    tax.parcel_id IS NOT NULL AND tax.report_generated_date>=CURRENT_DATE-90
    AND tax.reported_status='DELINQUENT' AND tax.delinquent_claimed
    AND tax.tax_year<EXTRACT(year FROM CURRENT_DATE)::integer
    AND tax.report_generated_date >= make_date(tax.tax_year+1,4,1)
  ) AS current_delinquency_verified,
  ov.owner_name,ov.current_site_address,ov.appraiser_just_value_usd,
  ov.source_url AS appraiser_source_url,
  (
    ov.parcel_id IS NOT NULL AND ov.verification_status='VERIFIED_SOURCE'
    AND ov.verified_at IS NOT NULL
    AND ov.source_checked_at>=now()-interval '90 days'
    AND ov.valuation_year>=EXTRACT(year FROM CURRENT_DATE)::integer-1
    AND ov.current_site_address ILIKE '%LEHIGH ACRES%'
  ) AS owner_value_verified,
  (
    ta.parcel_id IS NOT NULL AND ta.verification_status='VERIFIED_SOURCE'
    AND ta.verified_at IS NOT NULL
    AND ta.recorded_title_cleared AND ta.recorded_legal_access_verified
    AND ta.paved_road_verified
    AND ta.source_checked_at>=now()-interval '90 days'
  ) AS title_access_verified,
  COALESCE(r.status='GEOMETRY_UNRESOLVED',false) AS geometry_reconciliation_hold
FROM god_mode_ops.lee_parcel_qualification_screening s
LEFT JOIN intake i ON i.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.lee_parcel_geometry_evidence g ON g.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.lee_parcel_geometry_reconciliation r ON r.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.lee_parcel_fema_intersections f ON f.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.fema_nfhl_static_datasets fd ON fd.dataset_id=f.local_dataset_id
LEFT JOIN god_mode_ops.lee_parcel_nwi_intersections n ON n.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.nwi_static_datasets nd ON nd.dataset_id=n.dataset_id
LEFT JOIN tax ON tax.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.lee_verified_owner_valuation ov ON ov.parcel_id=s.parcel_id
LEFT JOIN god_mode_ops.lee_verified_title_access ta ON ta.parcel_id=s.parcel_id
WHERE s.county='Lee County'
)
SELECT b.*,
 CASE
  WHEN NOT b.geometry_verified OR b.geometry_reconciliation_hold
    THEN 'HOLD_GEOMETRY_RECONCILIATION'
  WHEN NOT b.fema_verified THEN 'HOLD_FEMA_EVIDENCE'
  WHEN NOT b.fema_clear THEN 'HOLD_FLOOD_REVIEW'
  WHEN NOT b.nwi_verified THEN 'HOLD_NWI_EVIDENCE'
  WHEN b.nwi_wetland_pct>=30 THEN 'REJECT_WETLANDS_THRESHOLD'
  WHEN b.nwi_wetland_pct>0 THEN 'HOLD_WETLAND_REVIEW'
  WHEN NOT b.current_tax_verified THEN 'HOLD_CURRENT_TAX_EVIDENCE'
  WHEN NOT b.current_delinquency_verified THEN 'HOLD_NO_VERIFIED_DISTRESS'
  WHEN NOT b.owner_value_verified THEN 'HOLD_OWNERSHIP_VALUATION'
  WHEN NOT b.title_access_verified THEN 'HOLD_TITLE_ACCESS'
  ELSE 'QUALIFIED_FOR_CONTACT_ENRICHMENT'
 END AS evidence_screening_status
FROM base b;
REVOKE ALL ON god_mode_ops.v_lee_master_screening_source FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS god_mode_ops.lee_master_screening (
  parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_qualification_screening(parcel_id),
  county text NOT NULL,
  historical_site_address text,
  geometry_verified boolean NOT NULL,
  geometry_source_url text,
  fema_verified boolean NOT NULL,
  fema_flood_zone text,
  fema_flood_category text,
  sfha_overlap_pct numeric,
  fema_source_url text,
  nwi_verified boolean NOT NULL,
  nwi_wetland_pct numeric,
  nwi_source_url text,
  current_tax_verified boolean NOT NULL,
  current_delinquency_verified boolean NOT NULL,
  verified_tax_year integer,
  tax_reported_status text,
  tax_source_url text,
  owner_value_verified boolean NOT NULL,
  owner_name text,
  appraiser_just_value_usd numeric,
  appraiser_source_url text,
  title_access_verified boolean NOT NULL,
  evidence_screening_status text NOT NULL,
  eligible_for_contact boolean NOT NULL DEFAULT false,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(NOT eligible_for_contact OR evidence_screening_status='QUALIFIED_FOR_CONTACT_ENRICHMENT'),
  CHECK(NOT eligible_for_contact OR (
     geometry_verified AND fema_verified AND nwi_verified
     AND current_tax_verified AND current_delinquency_verified
     AND owner_value_verified AND title_access_verified
     AND fema_flood_category='Low (X-Unshaded)'
     AND sfha_overlap_pct=0 AND nwi_wetland_pct=0
  ))
);
ALTER TABLE god_mode_ops.lee_master_screening ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.lee_master_screening FROM PUBLIC,anon,authenticated;
CREATE INDEX IF NOT EXISTS lee_master_screening_status_idx ON god_mode_ops.lee_master_screening(evidence_screening_status);

CREATE OR REPLACE FUNCTION god_mode_ops.refresh_lee_master_screening()
RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,god_mode_ops
AS $$
DECLARE updated integer;
BEGIN
  INSERT INTO god_mode_ops.lee_master_screening (
    parcel_id,county,historical_site_address,
    geometry_verified,geometry_source_url,
    fema_verified,fema_flood_zone,fema_flood_category,sfha_overlap_pct,fema_source_url,
    nwi_verified,nwi_wetland_pct,nwi_source_url,
    current_tax_verified,current_delinquency_verified,verified_tax_year,tax_reported_status,tax_source_url,
    owner_value_verified,owner_name,appraiser_just_value_usd,appraiser_source_url,
    title_access_verified,evidence_screening_status,eligible_for_contact,evaluated_at
  )
  SELECT
    parcel_id,county,historical_site_address,
    geometry_verified,geometry_source_url,
    fema_verified,fema_flood_zone,fema_flood_category,sfha_overlap_pct,fema_source_url,
    nwi_verified,nwi_wetland_pct,nwi_source_url,
    current_tax_verified,current_delinquency_verified,verified_tax_year,tax_reported_status,tax_source_url,
    owner_value_verified,owner_name,appraiser_just_value_usd,appraiser_source_url,
    title_access_verified,evidence_screening_status,
    evidence_screening_status='QUALIFIED_FOR_CONTACT_ENRICHMENT',now()
  FROM god_mode_ops.v_lee_master_screening_source
  ON CONFLICT (parcel_id) DO UPDATE SET
    county=EXCLUDED.county,
    historical_site_address=EXCLUDED.historical_site_address,
    geometry_verified=EXCLUDED.geometry_verified,
    geometry_source_url=EXCLUDED.geometry_source_url,
    fema_verified=EXCLUDED.fema_verified,
    fema_flood_zone=EXCLUDED.fema_flood_zone,
    fema_flood_category=EXCLUDED.fema_flood_category,
    sfha_overlap_pct=EXCLUDED.sfha_overlap_pct,
    fema_source_url=EXCLUDED.fema_source_url,
    nwi_verified=EXCLUDED.nwi_verified,
    nwi_wetland_pct=EXCLUDED.nwi_wetland_pct,
    nwi_source_url=EXCLUDED.nwi_source_url,
    current_tax_verified=EXCLUDED.current_tax_verified,
    current_delinquency_verified=EXCLUDED.current_delinquency_verified,
    verified_tax_year=EXCLUDED.verified_tax_year,
    tax_reported_status=EXCLUDED.tax_reported_status,
    tax_source_url=EXCLUDED.tax_source_url,
    owner_value_verified=EXCLUDED.owner_value_verified,
    owner_name=EXCLUDED.owner_name,
    appraiser_just_value_usd=EXCLUDED.appraiser_just_value_usd,
    appraiser_source_url=EXCLUDED.appraiser_source_url,
    title_access_verified=EXCLUDED.title_access_verified,
    evidence_screening_status=EXCLUDED.evidence_screening_status,
    eligible_for_contact=EXCLUDED.eligible_for_contact,
    evaluated_at=now();
  GET DIAGNOSTICS updated=ROW_COUNT;
  IF EXISTS (
    SELECT 1 FROM god_mode_ops.lee_master_screening
    WHERE eligible_for_contact AND (
     NOT geometry_verified OR NOT fema_verified OR NOT nwi_verified
     OR NOT current_tax_verified OR NOT current_delinquency_verified
     OR NOT owner_value_verified OR NOT title_access_verified
    )
  ) THEN RAISE EXCEPTION 'Unverified lead release detected'; END IF;
  RETURN updated;
END $$;
REVOKE ALL ON FUNCTION god_mode_ops.refresh_lee_master_screening() FROM PUBLIC,anon,authenticated;
