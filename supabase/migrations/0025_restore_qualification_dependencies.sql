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
