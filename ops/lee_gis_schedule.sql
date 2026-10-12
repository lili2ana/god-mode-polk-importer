-- Run only after gis_query.sql / lee_gis_queue.sql and their assertion scripts pass.
-- Update the existing job; do not create a second daily worker.
do $$ declare jid bigint;
begin
  select jobid into strict jid from cron.job
  where jobname='god_mode_lee_full_parcel_gis_daily';
  perform cron.alter_job(jid,command := 'select god_mode_ops.run_lee_gis_queue(2,false);');
end $$;
