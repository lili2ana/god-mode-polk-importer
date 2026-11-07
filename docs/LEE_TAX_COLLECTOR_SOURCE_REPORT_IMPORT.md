# Lee Tax Collector 2026 source and CI operator notes

The former `https://leetc.com/api/parcel/delinquency` endpoint is not an officially documented API. Do not use it. Lee County's official site links to a public reports portal at https://county-taxes.net/fl-lee/reports (GovHub/TaxSys). The portal requires JavaScript and may require an authorized report export. Do not infer that it has an unauthenticated API or bypass site access restrictions.

## Ingestion
Obtain an authorized **official** real estate tax report export, preserve the original file and its acquisition date, and normalize its columns to: `parcel_id,tax_year,tax_status,amount_due`. Never substitute an appraiser assessment roll for tax collector payment/certificate evidence.

Dry-run: `python lee_tax_collector_report_worker.py --csv report.csv --report-date 2026-11-07`.
Staging import: `STAGING_SUPABASE_DB_URL=... python lee_tax_collector_report_worker.py --csv report.csv --report-date 2026-11-07 --confirm-official-export --write`.

The importer only populates `god_mode_ops.lee_tax_report_import_batches` and `god_mode_ops.lee_tax_report_import_observations`, with `PENDING_PROVENANCE_REVIEW` and `REVIEW_REQUIRED` statuses. It never updates `lee_parcel_evidence_cache`, `qualification_status`, or `public.leads`. It never silently sets `delinquent_status=true`.

2026 Lee County tax bills were issued in October/November 2026 and are due March 31, 2027. `UNPAID` on November 7, 2026 **does not** establish delinquency. Lee County says delinquency begins April 1 of the following year. Historic 2024 reports also cannot prove current outstanding taxes in November 2026.

## CI
The one-time FEMA/NWI staging workflow run 42420904879 failed in its preflight: repository secret `STAGING_SUPABASE_DB_URL` was missing. Without a verified staging-only DB URL there is no source-backed import. Never substitute production credentials or write simulated evidence.
