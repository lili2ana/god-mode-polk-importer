-- Restore the two private source relations required by 003_judy_qualification_mapping.sql.
-- Column definitions verified against the existing production information_schema (read-only).
-- These are empty local CI tables, not imported qualification evidence.
CREATE TABLE IF NOT EXISTS god_mode_ops.buyer_demand_land_screening (
  parcel_id text NOT NULL,
  county text NOT NULL,
  city text NOT NULL,
  site_address text NOT NULL,
  owner_name text,
  gis_acres numeric,
  building_count integer,
  zoning text,
  source_url text NOT NULL,
  source_checked_at timestamptz NOT NULL DEFAULT now(),
  screening_status text NOT NULL DEFAULT 'PENDING_FLOOD_WETLAND_ROAD_DISTRESS',
  flood_status text NOT NULL DEFAULT 'UNVERIFIED',
  wetlands_status text NOT NULL DEFAULT 'UNVERIFIED',
  paved_road_status text NOT NULL DEFAULT 'UNVERIFIED',
  seller_distress_status text NOT NULL DEFAULT 'UNVERIFIED',
  contact_status text NOT NULL DEFAULT 'NOT_ENRICHED',
  notes text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS god_mode_ops.parcel_qualification_results (
  parcel_id text NOT NULL,
  status text NOT NULL,
  checks jsonb NOT NULL,
  evaluated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.buyer_demand_land_screening ENABLE ROW LEVEL SECURITY;
ALTER TABLE god_mode_ops.parcel_qualification_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.buyer_demand_land_screening, god_mode_ops.parcel_qualification_results FROM PUBLIC, anon, authenticated;

-- Read-only bridge from PR #17 qualification evidence to Judy review.
-- No automatic seller-card release; the source does not certify all gates.
CREATE OR REPLACE VIEW god_mode_ops.v_judy_qualification_mapping WITH (security_invoker=true) AS
SELECT q.parcel_id AS strap, s.county, s.owner_name, s.site_address AS property_address,
       q.status AS qualification_status,
       coalesce(q.checks->>'reason', array_to_string(ARRAY(SELECT jsonb_array_elements_text(coalesce(q.checks->'missing','[]'::jsonb))), ', '), 'UNVERIFIED') AS buildability_status,
       CASE WHEN coalesce((q.checks->>'eligible_for_contact')::boolean,false) IS TRUE
                 AND q.status='QUALIFIED' AND s.seller_distress_status='VERIFIED'
            THEN 'DISTRESS_REVIEW_REQUIRED' ELSE 'UNVERIFIED_DISTRESS' END AS financial_distress_reason,
       CASE WHEN coalesce((q.checks->>'eligible_for_contact')::boolean,false) IS TRUE
                 AND q.status='QUALIFIED' AND s.seller_distress_status='VERIFIED'
            THEN 'REVIEW_PROPERTY_WITH_OWNER' ELSE NULL END AS conversation_hook,
       false AS seller_card_release_authorized,
       q.evaluated_at
FROM god_mode_ops.parcel_qualification_results q
JOIN god_mode_ops.buyer_demand_land_screening s
  ON s.parcel_id=q.parcel_id AND lower(s.county) IN ('lee','charlotte');
REVOKE ALL ON god_mode_ops.v_judy_qualification_mapping FROM PUBLIC, anon, authenticated;
