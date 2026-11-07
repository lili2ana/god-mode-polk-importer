-- Lee County: unmatched historical tax-roll STRAPs are not qualified property geometry.
-- Private staging quarantine; status records remain unverified until source evidence resolves them.
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_parcel_geometry_reconciliation (
  parcel_id text PRIMARY KEY REFERENCES god_mode_ops.lee_parcel_qualification_screening(parcel_id),
  normalized_strap text NOT NULL CHECK (normalized_strap ~ '^[A-Z0-9]{17}$'),
  status text NOT NULL DEFAULT 'GEOMETRY_UNRESOLVED'
    CHECK (status IN ('GEOMETRY_UNRESOLVED','REVIEW_REQUIRED','RESOLVED_WITH_NEW_SOURCE')),
  lookup_source_url text NOT NULL
    CHECK (lookup_source_url LIKE 'https://gismapserver.leegov.com/%'),
  reconciliation_notes text NOT NULL DEFAULT 'Original intake STRAP has no exact matching saved current GIS polygon; do not infer source geometry.',
  queued_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.lee_parcel_geometry_reconciliation ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.lee_parcel_geometry_reconciliation FROM PUBLIC, anon, authenticated;
-- This reflects absent sourced polygons, not a claim of a fresh county REST request.
INSERT INTO god_mode_ops.lee_parcel_geometry_reconciliation(parcel_id, normalized_strap, status, lookup_source_url)
SELECT s.parcel_id, regexp_replace(upper(s.parcel_id),'[-.[:space:]]','','g'),
       'GEOMETRY_UNRESOLVED', 'https://gismapserver.leegov.com/gisserver910/rest/services/Layers/ParcelAddress/MapServer/0'
FROM god_mode_ops.lee_parcel_qualification_screening s
LEFT JOIN god_mode_ops.lee_parcel_geometry_evidence g ON g.parcel_id=s.parcel_id
WHERE s.county='Lee County' AND g.parcel_id IS NULL
ON CONFLICT (parcel_id) DO NOTHING;
