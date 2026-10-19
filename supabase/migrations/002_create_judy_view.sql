-- Staging-only, fail-closed Judy seller-card schema.
-- This migration does NOT assert legal buildability or ingest any records.
CREATE TABLE IF NOT EXISTS god_mode_ops.judy_screening_results (
  county text NOT NULL CHECK (county IN ('Lee','Charlotte')),
  strap text NOT NULL CHECK (length(trim(strap)) > 0),
  owner_name text,
  property_address text,
  contact_info text,
  distress_detail text,
  conversation_hook text,
  distress_verified boolean NOT NULL DEFAULT false,
  distress_source text,
  distress_checked_at timestamptz,
  wetlands_pct numeric(7,4) CHECK (wetlands_pct BETWEEN 0 AND 100),
  floodway_pct numeric(7,4) CHECK (floodway_pct BETWEEN 0 AND 100),
  flood_zone text,
  paved_frontage_verified boolean NOT NULL DEFAULT false,
  legal_access_verified boolean NOT NULL DEFAULT false,
  nwi_coverage_verified boolean NOT NULL DEFAULT false,
  flood_coverage_verified boolean NOT NULL DEFAULT false,
  zoning_verified boolean NOT NULL DEFAULT false,
  regulatory_wetlands_verified boolean NOT NULL DEFAULT false,
  title_verified boolean NOT NULL DEFAULT false,
  screening_checked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (county, strap)
);
ALTER TABLE god_mode_ops.judy_screening_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.judy_screening_results FROM PUBLIC, anon, authenticated;
CREATE OR REPLACE VIEW public.v_judy_seller_cards
WITH (security_invoker = true) AS
SELECT county, strap, owner_name, contact_info, property_address,
       distress_detail,
       format('Paved frontage verified; %s%% mapped wetlands; Flood Zone %s',
              wetlands_pct, coalesce(flood_zone, 'unknown')) AS buildability_summary,
       conversation_hook, screening_checked_at
FROM god_mode_ops.judy_screening_results
WHERE distress_verified IS TRUE
  AND distress_source IS NOT NULL
  AND distress_checked_at IS NOT NULL
  AND wetlands_pct <= 30
  AND floodway_pct < 100
  AND paved_frontage_verified IS TRUE
  AND legal_access_verified IS TRUE
  AND nwi_coverage_verified IS TRUE
  AND flood_coverage_verified IS TRUE
  AND zoning_verified IS TRUE
  AND regulatory_wetlands_verified IS TRUE
  AND title_verified IS TRUE;
REVOKE ALL ON public.v_judy_seller_cards FROM PUBLIC, anon, authenticated;
-- Service-role visibility must be granted explicitly after policy review.
