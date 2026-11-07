-- Private observations only: no direct delinquency or lead promotion.
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_tax_report_import_batches (
 batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 source_url text NOT NULL CHECK(source_url='https://county-taxes.net/fl-lee/reports'),
 report_generated_date date NOT NULL,
 archive_sha256 text NOT NULL UNIQUE CHECK(archive_sha256 ~ '^[0-9a-f]{64}$'),
 row_count integer NOT NULL CHECK(row_count > 0),
 validation_status text NOT NULL DEFAULT 'PENDING_PROVENANCE_REVIEW'
 CHECK(validation_status IN ('PENDING_PROVENANCE_REVIEW','VERIFIED_SOURCE','REJECTED_SOURCE')),
 loaded_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.lee_tax_report_import_batches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.lee_tax_report_import_batches FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_tax_report_import_observations (
 batch_id uuid NOT NULL REFERENCES god_mode_ops.lee_tax_report_import_batches(batch_id),
 parcel_id text NOT NULL REFERENCES god_mode_ops.lee_parcel_qualification_screening(parcel_id),
 normalized_strap text NOT NULL CHECK(normalized_strap ~ '^[A-Z0-9]{17}$'),
 tax_year integer NOT NULL CHECK(tax_year BETWEEN 2000 AND 2100),
 reported_status text NOT NULL CHECK(reported_status IN ('PAID','UNPAID','DELINQUENT','CERTIFICATE','REDEEMED','UNKNOWN')),
 amount_due numeric CHECK(amount_due IS NULL OR amount_due >= 0),
 delinquent_claimed boolean NOT NULL DEFAULT false,
 review_status text NOT NULL DEFAULT 'REVIEW_REQUIRED' CHECK(review_status='REVIEW_REQUIRED'),
 imported_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(batch_id,parcel_id,tax_year),
 CHECK(NOT delinquent_claimed OR reported_status='DELINQUENT')
);
ALTER TABLE god_mode_ops.lee_tax_report_import_observations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.lee_tax_report_import_observations FROM PUBLIC, anon, authenticated;
CREATE INDEX IF NOT EXISTS lee_tax_import_obs_parcel_idx ON god_mode_ops.lee_tax_report_import_observations(parcel_id,tax_year);
