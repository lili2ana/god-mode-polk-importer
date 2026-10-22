-- Minimal production-shaped due diligence review table for ephemeral replay.
-- No records are seeded; legacy quarantine must operate on an empty table.
CREATE TABLE IF NOT EXISTS public.due_diligence_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 property_id uuid NOT NULL REFERENCES public.properties(id),
 lead_id uuid,
 status text NOT NULL DEFAULT 'queued',
 title_status text NOT NULL DEFAULT 'pending',
 tax_status text NOT NULL DEFAULT 'pending',
 zoning_status text NOT NULL DEFAULT 'pending',
 access_status text NOT NULL DEFAULT 'pending',
 utilities_status text NOT NULL DEFAULT 'pending',
 flood_status text NOT NULL DEFAULT 'pending',
 wetlands_status text NOT NULL DEFAULT 'pending',
 comps_status text NOT NULL DEFAULT 'pending',
 exit_status text NOT NULL DEFAULT 'pending',
 underwriting_status text NOT NULL DEFAULT 'pending',
 dd_score numeric NOT NULL DEFAULT 0,
 fatal_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
 findings jsonb NOT NULL DEFAULT '{}'::jsonb,
 started_at timestamptz,
 completed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.due_diligence_reviews ENABLE ROW LEVEL SECURITY;
