
CREATE TABLE IF NOT EXISTS god_mode_ops.lee_appraiser_import_batches (
 batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 source_url text NOT NULL CHECK (
   source_url IN ('https://www.leepa.org/TaxRoll/ParcelData/LCPA_Parcel_Data_TXT.zip',
                  'https://www.leepa.org/OnlineReports/Reports.aspx')),
 archive_sha256 text NOT NULL UNIQUE CHECK(archive_sha256 ~ '^[0-9a-f]{64}$'),
 report_date date NOT NULL,
 valuation_year integer NOT NULL CHECK(valuation_year BETWEEN 2020 AND 2100),
 row_count integer NOT NULL CHECK(row_count>0),
 validation_status text NOT NULL DEFAULT 'PENDING_PROVENANCE_REVIEW'
  CHECK(validation_status IN ('PENDING_PROVENANCE_REVIEW','VERIFIED_SOURCE','REJECTED_SOURCE')),
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.lee_appraiser_import_batches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE god_mode_ops.lee_appraiser_import_batches FROM PUBLIC,anon,authenticated;
ALTER TABLE god_mode_ops.lee_verified_owner_valuation
 ADD COLUMN IF NOT EXISTS import_batch_id uuid
 REFERENCES god_mode_ops.lee_appraiser_import_batches(batch_id);
CREATE INDEX IF NOT EXISTS lee_appraiser_import_batches_status_idx
 ON god_mode_ops.lee_appraiser_import_batches(validation_status);
