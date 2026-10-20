-- Reconstruct the two historical named schedules for clean ephemeral migration replay.
-- Names, schedules and commands were checked against the production cron catalog.
-- Freshly reconstructed jobs are disabled: this migration must not start ingestion.
-- Existing jobs are never overwritten; the later legacy migration validates them.
DO $$
DECLARE job_id bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'god_mode_polk_daily_sync') THEN
    SELECT cron.schedule('god_mode_polk_daily_sync', '17 5 * * *',
      'select public.full_daily_market_refresh();') INTO job_id;
    PERFORM cron.alter_job(job_id, active := false);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'god_mode_refresh_target_parcels') THEN
    SELECT cron.schedule('god_mode_refresh_target_parcels', '27 5 * * *',
      'select god_mode_ops.refresh_target_parcels();') INTO job_id;
    PERFORM cron.alter_job(job_id, active := false);
  END IF;
END $$;
