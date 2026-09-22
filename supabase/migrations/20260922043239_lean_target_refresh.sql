-- Only derived target registry entries may be removed; countywide tables are untouched.
CREATE OR REPLACE FUNCTION god_mode_ops.refresh_target_parcels()
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE n bigint;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(732190402);
  LOCK TABLE public.properties IN SHARE MODE;
  LOCK TABLE god_mode_ops.target_parcels IN EXCLUSIVE MODE;
  IF EXISTS (SELECT 1 FROM public.properties WHERE parcel_id IS NOT NULL
      AND btrim(parcel_id) <> ''
      AND regexp_replace(upper(parcel_id),'[^A-Z0-9]','','g') !~ '^[0-9]{18}$') THEN
    RAISE EXCEPTION 'Invalid property parcel key; target refresh refused';
  END IF;
  IF EXISTS (SELECT 1 FROM public.properties WHERE parcel_id IS NOT NULL AND btrim(parcel_id) <> ''
      GROUP BY regexp_replace(upper(parcel_id),'[^A-Z0-9]','','g') HAVING count(*) > 1) THEN
    RAISE EXCEPTION 'Duplicate normalized property key; target refresh refused';
  END IF;
  DELETE FROM god_mode_ops.target_parcels t WHERE NOT EXISTS (
    SELECT 1 FROM public.properties p WHERE p.id=t.property_id
      AND p.parcel_id IS NOT NULL AND btrim(p.parcel_id)<>''
      AND regexp_replace(upper(p.parcel_id),'[^A-Z0-9]','','g')=t.parcel_key);
  INSERT INTO god_mode_ops.target_parcels(property_id,parcel_id,parcel_key,refreshed_at)
    SELECT id,parcel_id,regexp_replace(upper(parcel_id),'[^A-Z0-9]','','g'),now()
    FROM public.properties WHERE parcel_id IS NOT NULL AND btrim(parcel_id)<>''
    ON CONFLICT(property_id) DO UPDATE SET parcel_id=excluded.parcel_id,
      parcel_key=excluded.parcel_key,refreshed_at=excluded.refreshed_at
    WHERE (target_parcels.parcel_id,target_parcels.parcel_key)
      IS DISTINCT FROM (excluded.parcel_id,excluded.parcel_key);
  SELECT count(*) INTO n FROM god_mode_ops.target_parcels;
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION god_mode_ops.refresh_target_parcels() FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION god_mode_ops.refresh_market_and_targets()
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE result jsonb; targets bigint;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(732190402);
  result := public.full_daily_market_refresh();
  IF result->>'fetched' IS NULL OR (result->>'fetched')::integer <= 0
      OR (result->>'fetched')::integer >= 40000 THEN
    RAISE EXCEPTION 'Upstream refresh empty or reached its 40000-row cap; refusing completion';
  END IF;
  targets := god_mode_ops.refresh_target_parcels();
  RETURN result || jsonb_build_object('target_parcels',targets);
END $$;
REVOKE ALL ON FUNCTION god_mode_ops.refresh_market_and_targets() FROM PUBLIC, anon, authenticated;
ALTER TABLE god_mode_ops.target_parcels ENABLE ROW LEVEL SECURITY;
ALTER TABLE god_mode_ops.feed_retention_policy ENABLE ROW LEVEL SECURITY;

-- Preserve the existing daily schedule (05:17 GMT). Chain in one transaction.
-- Disable the timer-based second job; do not delete its configuration/history.
DO $$
DECLARE upstream bigint; downstream bigint;
BEGIN
  SELECT jobid INTO STRICT upstream FROM cron.job WHERE jobname='god_mode_polk_daily_sync';
  SELECT jobid INTO STRICT downstream FROM cron.job WHERE jobname='god_mode_refresh_target_parcels';
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobid=upstream AND username='postgres'
      AND command IN ('select public.full_daily_market_refresh();',
                      'select god_mode_ops.refresh_market_and_targets();')) THEN
    RAISE EXCEPTION 'Unexpected upstream cron definition; refusing replacement';
  END IF;
  PERFORM cron.alter_job(upstream,command:='select god_mode_ops.refresh_market_and_targets();');
  PERFORM cron.alter_job(downstream,active:=false);
END $$;
