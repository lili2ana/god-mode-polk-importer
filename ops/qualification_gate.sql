create table if not exists god_mode_ops.parcel_access_reviews (
  parcel_id text primary key references god_mode_ops.buyer_demand_land_screening(parcel_id),
  status text not null default 'REVIEW_REQUIRED' check(status in ('REVIEW_REQUIRED','VERIFIED','REJECTED')),
  document_url text, document_sha256 text, instrument_reference text,
  recorded_frontage_ft numeric, parcel_identity_verified boolean not null default false,
  legal_access_verified boolean not null default false, paved_frontage_verified boolean not null default false,
  current_restrictions_checked boolean not null default false,
  reviewed_by uuid, reviewed_at timestamptz, expires_at timestamptz,
  research jsonb not null default '{}',
  check(status <> 'VERIFIED' or (
    document_url like 'https://%' and document_sha256 ~ '^[a-f0-9]{64}$'
    and length(trim(instrument_reference))>0 and recorded_frontage_ft>0
    and parcel_identity_verified and legal_access_verified and paved_frontage_verified
    and current_restrictions_checked and reviewed_by is not null
    and reviewed_at is not null and expires_at>reviewed_at
    and document_url is not null and document_sha256 is not null
    and instrument_reference is not null and recorded_frontage_ft is not null and expires_at is not null
  ))
);
alter table god_mode_ops.parcel_access_reviews enable row level security;
revoke all on god_mode_ops.parcel_access_reviews from public,anon,authenticated;
grant select,insert,update on god_mode_ops.parcel_access_reviews to service_role;

create table if not exists god_mode_ops.parcel_qualification_results (
  parcel_id text primary key references god_mode_ops.buyer_demand_land_screening(parcel_id),
  status text not null, checks jsonb not null, evaluated_at timestamptz not null default now()
);
alter table god_mode_ops.parcel_qualification_results enable row level security;
revoke all on god_mode_ops.parcel_qualification_results from public,anon,authenticated;
grant select,insert,update on god_mode_ops.parcel_qualification_results to service_role;

create or replace function god_mode_ops.evaluate_gis_gate(c jsonb)
returns jsonb language plpgsql immutable set search_path = '' as $$
declare missing jsonb := '[]'; flood jsonb := c->'flood'; features jsonb; f jsonb;
  low_risk boolean := true;
begin
  features := flood->'features';
  if flood->>'result'='FOUND' and jsonb_typeof(features)='array' and jsonb_array_length(features)>0 then
    for f in select value from jsonb_array_elements(features) loop
      if f->'attributes'->>'SFHA_TF'='T' or left(f->'attributes'->>'FLD_ZONE',1) in ('A','V') then
        return '{"status":"REJECTED_FLOOD_ZONE","eligible_for_contact":false,"reason":"full_polygon_flood_hazard"}';
      end if;
      if f->'attributes'->>'FLD_ZONE' is distinct from 'X'
        or f->'attributes'->>'SFHA_TF' is distinct from 'F'
        or f->'attributes'->>'ZONE_SUBTY' is distinct from 'AREA OF MINIMAL FLOOD HAZARD' then low_risk:=false; end if;
    end loop;
  else low_risk:=false; end if;
  if not low_risk then missing := missing || '"FLOOD"'::jsonb; end if;
  if c->'wetlands'->>'result'='FOUND' then
    return '{"status":"REJECTED_MAPPED_WETLANDS","eligible_for_contact":false}';
  end if;
  if c->'wetlands'->>'result' is distinct from 'NOT_FOUND'
    or c->'wetlands'->>'feature_count' is distinct from '0' then missing := missing || '"WETLANDS"'::jsonb; end if;
  if c->'road'->>'result' is distinct from 'FOUND'
    or not coalesce(c->'road'->'paved_candidate_count' > '0'::jsonb,false) then missing := missing || '"PAVED_ROAD"'::jsonb; end if;
  if c->'legal' is distinct from 'true'::jsonb then missing:=missing || '"RECORDED_FRONTAGE_AND_ACCESS_REVIEW"'::jsonb; end if;
  if c->'distress' is distinct from 'true'::jsonb then missing:=missing || '"SELLER_DISTRESS"'::jsonb; end if;
  if c->'initial_checks' is distinct from 'true'::jsonb then missing:=missing || '"SIZE_ZONING_ROUGH_COMPS"'::jsonb; end if;
  if jsonb_array_length(missing)>0 then
    return jsonb_build_object('status','HOLD','eligible_for_contact',false,'missing',missing);
  end if;
  return '{"status":"QUALIFIED_FOR_CONTACT_ENRICHMENT","eligible_for_contact":true}';
end $$;

create or replace function god_mode_ops.refresh_county_qualification()
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r record; e record; checks jsonb; gate jsonb; n integer:=0; qualified integer:=0;
begin
  if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
  for r in select s.*,a.status access_status,a.expires_at access_expires,p.id property_id
    from god_mode_ops.buyer_demand_land_screening s
    left join god_mode_ops.parcel_access_reviews a on a.parcel_id=s.parcel_id
    left join public.properties p on p.parcel_id=s.parcel_id and lower(p.county)=lower(s.county)
    where s.screening_status in ('PENDING_FLOOD_WETLAND_ROAD_DISTRESS','QUALIFIED_FOR_CONTACT_ENRICHMENT')
  loop
    checks := '{}';
    for e in select distinct on (source) source,result,details,checked_at
      from god_mode_ops.gis_worker_evidence where parcel_id=r.parcel_id
      and source in ('FEMA_FLOOD','USFWS_WETLANDS',upper(r.county)||'_ROAD')
      order by source,checked_at desc,id desc
    loop
      checks := checks || jsonb_build_object(case e.source when 'FEMA_FLOOD' then 'flood'
        when 'USFWS_WETLANDS' then 'wetlands' else 'road' end,
        case when e.checked_at between now()-interval '7 days' and now()
        then e.details || jsonb_build_object('result',e.result) else '{"result":"STALE"}'::jsonb end);
    end loop;
    checks := checks || jsonb_build_object('legal',coalesce(r.access_status='VERIFIED' and r.access_expires>now(),false),
      'distress',god_mode_ops.county_distress_verified(r.parcel_id,r.county),
      'initial_checks',coalesce(r.building_count=0 and r.gis_acres>=case when lower(r.city)='lehigh acres' then 0.25 else 10000.0/43560.0 end
        and god_mode_ops.county_preflight_pass((select d.findings->'county_preflight' from public.due_diligence_reviews d
          where d.property_id=r.property_id),r.county,r.parcel_id,r.gis_acres),false));
    gate := god_mode_ops.evaluate_gis_gate(checks);
    insert into god_mode_ops.parcel_access_reviews(parcel_id,research)
    values(r.parcel_id,jsonb_build_object('county',r.county,'search_url',case lower(r.county)
      when 'lee' then 'https://or.leeclerk.org/LandMarkWeb/' when 'charlotte' then 'https://recording.charlotteclerk.com/Search/Legal' else null end))
    on conflict(parcel_id) do nothing;
    insert into god_mode_ops.parcel_qualification_results(parcel_id,status,checks,evaluated_at)
    values(r.parcel_id,gate->>'status',gate || jsonb_build_object('inputs',checks),now())
    on conflict(parcel_id) do update set status=excluded.status,checks=excluded.checks,evaluated_at=excluded.evaluated_at;
    if gate->>'status' like 'REJECTED%' then
      update god_mode_ops.buyer_demand_land_screening set screening_status=gate->>'status',
        flood_status=case when gate->>'status'='REJECTED_FLOOD_ZONE' then 'FAIL' else flood_status end,
        wetlands_status=case when gate->>'status'='REJECTED_MAPPED_WETLANDS' then 'FAIL' else wetlands_status end,
        updated_at=now() where parcel_id=r.parcel_id;
    elsif gate->>'eligible_for_contact'='true' then
      update god_mode_ops.buyer_demand_land_screening set screening_status='QUALIFIED_FOR_CONTACT_ENRICHMENT',
        flood_status='PASS',wetlands_status='PASS',paved_road_status='PASS',seller_distress_status='VERIFIED',updated_at=now()
      where parcel_id=r.parcel_id;
      qualified:=qualified+1;
    else
      update god_mode_ops.buyer_demand_land_screening set screening_status='PENDING_FLOOD_WETLAND_ROAD_DISTRESS',
        flood_status='REVIEW',wetlands_status='REVIEW',paved_road_status='REVIEW',updated_at=now() where parcel_id=r.parcel_id;
    end if;
    n:=n+1;
  end loop;
  return jsonb_build_object('evaluated',n,'qualified',qualified,'outreach_sent',0);
end $$;
revoke all on function god_mode_ops.evaluate_gis_gate(jsonb),god_mode_ops.refresh_county_qualification() from public,anon,authenticated;
grant execute on function god_mode_ops.evaluate_gis_gate(jsonb),god_mode_ops.refresh_county_qualification() to service_role;
