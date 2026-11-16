-- FEMA source architecture changes to local static NFHL county geodatabase intersection.
-- Do NOT claim FEMA data was downloaded or parcels were verified by this migration.
UPDATE god_mode_ops.lee_parcel_api_jobs
SET job_status='PIVOT_LOCAL_POSTGIS_INTERSECTION',
    endpoint_url='https://msc.fema.gov/portal/downloadProduct?productID=NFHL_12071C',
    endpoint_verified=false,
    parcels_processed=0,
    block_reason='PostGIS parcel cache staged; awaiting download and validation of official FEMA NFHL_12071C archive. No verified FEMA hazard or intersection records yet.',
    updated_at=now()
WHERE source_name='FEMA_NFHL'
  AND parcels_processed=0
  AND job_status IN
    ('REGISTERED_PENDING_PARCEL_GEOMETRY','BLOCKED_FEMA_LIVE_RESPONSE','PIVOT_LOCAL_POSTGIS_INTERSECTION');
