-- Verified FEMA NFHL polygon-overlay results, isolated from lead qualification.
-- Never treat this table as proof of tax delinquency, legal access or owner match.
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_parcel_fema_intersections (
    parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_geometry_evidence(parcel_id),
    fema_flood_zone text NOT NULL,
    fema_flood_category text NOT NULL CHECK (
        fema_flood_category IN ('High Risk (A/V)', 'Moderate (X-Shaded)',
            'Low (X-Unshaded)', 'Undetermined (D)', 'Open Water')
    ),
    sfha_overlap_pct numeric(7,4) NOT NULL CHECK (sfha_overlap_pct BETWEEN 0 AND 100),
    mixed_zones boolean NOT NULL,
    review_status text NOT NULL CHECK (review_status IN ('REVIEW_REQUIRED', 'EVIDENCE_CAPTURED')),
    overlap_breakdown jsonb NOT NULL CHECK (jsonb_typeof(overlap_breakdown)='array'),
    source_feature_count integer NOT NULL CHECK (source_feature_count > 0),
    source_url text NOT NULL CHECK (source_url LIKE 'https://hazards.fema.gov/%'),
    source_checked_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.lee_parcel_fema_intersections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.lee_parcel_fema_intersections FROM PUBLIC, anon, authenticated;
