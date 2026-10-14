-- Store preliminary county screening in the existing app DD queue without
-- bypassing its seller-qualification trigger or creating outreach leads.
create or replace function god_mode_ops.sync_county_dd_holds()
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r record; pid uuid; n integer:=0;
begin
  if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
  for r in select s.*,q.status gate_status,q.checks,q.evaluated_at
    from god_mode_ops.buyer_demand_land_screening s
    join god_mode_ops.parcel_qualification_results q using(parcel_id)
    where exists(select 1 from god_mode_ops.gis_worker_evidence e
      where e.parcel_id=s.parcel_id and e.source=upper(s.county)||'_PARCEL_GEOMETRY'
      and e.details->>'worker'='county_queue_v1' and e.details->>'identity_verified'='true')
  loop
    insert into public.properties(parcel_id,address,city,state,county,property_type,acreage,owner_name,
      source_name,source_url,source_verified_at,raw_data)
    values(r.parcel_id,r.site_address,r.city,'FL',r.county,'land',r.gis_acres,r.owner_name,
      'county_gis_screening',r.source_url,r.source_checked_at,jsonb_build_object('county_screening',r.checks))
    on conflict(parcel_id) do update set raw_data=coalesce(properties.raw_data,'{}') || excluded.raw_data
    where lower(properties.county)=lower(excluded.county)
    returning id into pid;
    if pid is null then continue; end if;
    insert into public.due_diligence_reviews(property_id,status,flood_status,wetlands_status,access_status,
      findings,fatal_flags,updated_at)
    values(pid,'qualification_hold',case when r.gate_status='REJECTED_FLOOD_ZONE' then 'failed' else 'review_required' end,
      case when r.gate_status='REJECTED_MAPPED_WETLANDS' then 'failed' else 'review_required' end,'review_required',
      jsonb_build_object('county_screening',r.checks,'screening_checked_at',r.evaluated_at,
        'deep_dd_completed',false,'exact_frontage_verified',false,'legal_access_verified',false),
      case when r.gate_status like 'REJECTED%' then jsonb_build_array(r.gate_status) else '[]'::jsonb end,now())
    on conflict(property_id) do update set findings=coalesce(due_diligence_reviews.findings,'{}')||excluded.findings,
      flood_status=excluded.flood_status,wetlands_status=excluded.wetlands_status,
      access_status=excluded.access_status,fatal_flags=excluded.fatal_flags,updated_at=excluded.updated_at
    where due_diligence_reviews.status='qualification_hold';
    n:=n+1;
  end loop;
  return jsonb_build_object('synced',n,'deep_dd_completed',0,'outreach_leads_created',0);
end $$;
revoke all on function god_mode_ops.sync_county_dd_holds() from public,anon,authenticated;
grant execute on function god_mode_ops.sync_county_dd_holds() to service_role;
