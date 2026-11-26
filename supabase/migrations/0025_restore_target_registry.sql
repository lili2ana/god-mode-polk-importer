-- Reconstructed from read-only production catalog inspection (2026-10-19).
-- Ephemeral baseline: never seed parcel evidence or synthetic cron jobs.
CREATE TABLE IF NOT EXISTS public.properties (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parcel_id text UNIQUE
);
CREATE TABLE IF NOT EXISTS god_mode_ops.target_parcels (
  property_id uuid PRIMARY KEY REFERENCES public.properties(id) ON DELETE CASCADE,
  parcel_id text NOT NULL,
  parcel_key text NOT NULL UNIQUE,
  refreshed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS god_mode_ops.feed_retention_policy (
  feed_name text PRIMARY KEY,
  retention_mode text NOT NULL,
  retention_detail text NOT NULL,
  destructive_cleanup_enabled boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE god_mode_ops.target_parcels ENABLE ROW LEVEL SECURITY;
ALTER TABLE god_mode_ops.feed_retention_policy ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.target_parcels, god_mode_ops.feed_retention_policy FROM PUBLIC, anon, authenticated;
