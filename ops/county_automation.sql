create or replace function god_mode_ops.materialize_county_qualified_leads()
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare n integer;
begin
  if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
  if not pg_try_advisory_xact_lock(714426003) then return '{"created":0,"busy":true}'; end if;
  insert into public.leads(property_id,status,priority,outreach_status,notes)
  select p.id,'qualified_for_contact_enrichment','normal','held',
    'County qualification gates verified. Contact enrichment pending; no outreach sent.'
  from god_mode_ops.buyer_demand_land_screening s
  join god_mode_ops.parcel_qualification_results q using(parcel_id)
  join public.properties p on p.parcel_id=s.parcel_id and lower(p.county)=lower(s.county)
  join god_mode_ops.parcel_access_reviews a on a.parcel_id=s.parcel_id
  where s.screening_status='QUALIFIED_FOR_CONTACT_ENRICHMENT'
    and q.status='QUALIFIED_FOR_CONTACT_ENRICHMENT' and q.checks->'eligible_for_contact'='true'::jsonb
    and q.evaluated_at between now()-interval '1 hour' and now()
    and a.status='VERIFIED' and a.expires_at>now()
    and not exists(select 1 from public.leads l where l.property_id=p.id);
  get diagnostics n=row_count;
  return jsonb_build_object('created',n,'outreach_sent',0);
end $$;

create or replace function god_mode_ops.run_county_automation(p_limit integer default 2,p_force boolean default false)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare gis_result jsonb; qualification jsonb; dd_result jsonb; leads_result jsonb; market_result jsonb;
begin
  if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
  gis_result := god_mode_ops.run_county_gis_queue(null,p_limit,p_force);
  perform god_mode_ops.refresh_county_qualification();
  perform god_mode_ops.sync_county_dd_holds();
  market_result := god_mode_ops.refresh_lee_market_evidence(p_limit);
  qualification := god_mode_ops.refresh_county_qualification();
  dd_result := god_mode_ops.sync_county_dd_holds();
  leads_result := god_mode_ops.materialize_county_qualified_leads();
  return jsonb_build_object('gis',gis_result,'market',market_result,'qualification',qualification,'dd',dd_result,'leads',leads_result);
end $$;
revoke all on function god_mode_ops.materialize_county_qualified_leads(),god_mode_ops.run_county_automation(integer,boolean) from public,anon,authenticated;
grant execute on function god_mode_ops.materialize_county_qualified_leads(),god_mode_ops.run_county_automation(integer,boolean) to service_role;

-- Reuse job 11: its historic Lee name is retained to avoid duplicate jobs.
do $$ declare jid bigint;
begin
  select jobid into strict jid from cron.job where jobname='god_mode_lee_full_parcel_gis_daily';
  perform cron.alter_job(jid,command := 'select god_mode_ops.run_county_automation(2,false);');
end $$;
