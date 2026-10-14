-- Nightly public-authoritative Lee GIS view, owner LeeCountyFLGIS, item
-- a69c335ad6e54fc0872f7ed47208f11e. No paid credentials or clerk bypass.
create table if not exists god_mode_ops.county_market_evidence (
 parcel_id text primary key references god_mode_ops.buyer_demand_land_screening(parcel_id),
 county text not null, source_url text not null, checked_at timestamptz not null,
 subject jsonb not null, comparable_response jsonb not null, findings jsonb not null
);
alter table god_mode_ops.county_market_evidence enable row level security;
revoke all on god_mode_ops.county_market_evidence from public,anon,authenticated;
grant select,insert,update on god_mode_ops.county_market_evidence to service_role;

create or replace function god_mode_ops.lee_comp_usable(a jsonb,s jsonb)
returns boolean language plpgsql stable set search_path='' as $$
begin
 return coalesce(a->>'STRAP'<>s->>'STRAP' and a->>'ZONING'=s->>'ZONING'
  and a->>'S_1VI'='V' and a->>'S_1TC' in ('01','02') and a->>'S_1TOC' in ('01','02')
  and length(a->>'S_1OR_NUM')>0 and (a->>'S_1AMOUNT')::numeric>100
  and (a->>'GISACRES')::numeric between (s->>'GISACRES')::numeric*0.5 and (s->>'GISACRES')::numeric*2
  and to_timestamp((a->>'S_1DATE')::double precision/1000)::date between (current_date-interval '2 years')::date and current_date
  and (a->>'LONGITUDE')::double precision between -180 and 180
  and (a->>'LATITUDE')::double precision between -90 and 90
  and extensions.st_distance(
    extensions.st_setsrid(extensions.st_makepoint((a->>'LONGITUDE')::float8,(a->>'LATITUDE')::float8),4326)::extensions.geography,
    extensions.st_setsrid(extensions.st_makepoint((s->>'LONGITUDE')::float8,(s->>'LATITUDE')::float8),4326)::extensions.geography)<=2000,false);
exception when others then return false;
end $$;

create or replace function god_mode_ops.refresh_lee_market_evidence(p_limit integer default 2)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r record; a jsonb; resp jsonb; comps jsonb; samples jsonb; f jsonb; lon numeric; lat numeric;
 endpoint constant text:='https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0/query';
 n integer:=0; failed integer:=0; evidence_findings jsonb; pid uuid;
begin
 if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
 if not pg_try_advisory_xact_lock(714426004) then return '{"busy":true}'; end if;
 for r in select s.* from god_mode_ops.buyer_demand_land_screening s
 where lower(county)='lee' and screening_status='PENDING_FLOOD_WETLAND_ROAD_DISTRESS'
 order by updated_at,parcel_id limit least(greatest(p_limit,1),5)
 loop
  resp:=god_mode_ops.gis_query(endpoint,jsonb_build_object('where',format('STRAP=%L',r.parcel_id),'outFields','*','returnGeometry','false'));
  if resp->'ok' is distinct from 'true'::jsonb or jsonb_array_length(resp->'data'->'features')<>1 then failed:=failed+1; continue; end if;
  a:=resp->'data'->'features'->0->'attributes';
  if a->>'STRAP' is distinct from r.parcel_id or a->>'LONGITUDE' is null or a->>'LATITUDE' is null then failed:=failed+1; continue; end if;
  lon:=(a->>'LONGITUDE')::numeric;lat:=(a->>'LATITUDE')::numeric;
  comps:=god_mode_ops.gis_query(endpoint,jsonb_build_object('where',
   format('DORCODE=''00'' AND S_1VI=''V'' AND S_1TC IN (''01'',''02'') AND S_1TOC IN (''01'',''02'') AND ZONING=%L AND GISACRES BETWEEN %s AND %s AND S_1DATE >= DATE %L',a->>'ZONING',(a->>'GISACRES')::numeric*0.5,(a->>'GISACRES')::numeric*2,(current_date-interval '2 years')::date),
   'geometry',format('%s,%s,%s,%s',lon-0.025,lat-0.02,lon+0.025,lat+0.02),'geometryType','esriGeometryEnvelope','inSR','4326',
   'spatialRel','esriSpatialRelIntersects','outFields','STRAP,GISACRES,ZONING,S_1DATE,S_1AMOUNT,S_1VI,S_1TC,S_1TOC,S_1OR_NUM,LONGITUDE,LATITUDE',
   'returnGeometry','false','resultRecordCount',1000));
  samples:='[]';
  if comps->'ok'='true'::jsonb then
   -- One sale per instrument avoids duplicate/multiple-parcel price inflation.
   for f in select distinct on (value->'attributes'->>'S_1OR_NUM') value->'attributes'
    from jsonb_array_elements(comps->'data'->'features')
    where god_mode_ops.lee_comp_usable(value->'attributes',a)
    order by value->'attributes'->>'S_1OR_NUM',value->'attributes'->>'STRAP'
   loop
    samples:=samples||jsonb_build_array(jsonb_build_object('parcel_id',f->>'STRAP','county','Lee','asset_type','land','verified',true,
      'price',f->'S_1AMOUNT','acreage',f->'GISACRES','sale_date',to_timestamp((f->>'S_1DATE')::float8/1000)::date,
      'instrument',f->>'S_1OR_NUM','qualification_code',f->>'S_1TOC','source_url',endpoint,'raw',f));
   end loop;
  end if;
  evidence_findings:=jsonb_build_object('county','Lee','checked_at',now(),'source_verified',comps->'ok'='true'::jsonb,
   'zoning',a->>'ZONING','zoning_verified',a->>'ZONING'='RS-1','sample',samples,
   'scope','rough land comps; no appraisal or buildability approval','source_url',endpoint,
   'seller_indicators',jsonb_build_object('status','REVIEW_REQUIRED','owner',a->>'O_NAME',
    'mailing_city',a->>'O_CITY','mailing_state',a->>'O_STATE','latest_sale_ms',a->'S_1DATE',
    'reason','Mailing address and sale history are indicators only; current adverse records and ownership duration need verification'));
  insert into god_mode_ops.county_market_evidence values(r.parcel_id,'Lee',endpoint,now(),a,comps,evidence_findings)
   on conflict(parcel_id) do update set checked_at=excluded.checked_at,subject=excluded.subject,
    comparable_response=excluded.comparable_response,findings=excluded.findings;
  select id into pid from public.properties where parcel_id=r.parcel_id and lower(county)='lee';
  update public.due_diligence_reviews set findings=coalesce(due_diligence_reviews.findings,'{}')||jsonb_build_object('county_preflight',evidence_findings),
   updated_at=now() where property_id=pid and status='qualification_hold';
  n:=n+1;
 end loop;
 return jsonb_build_object('refreshed',n,'failed',failed,'distress_auto_approved',0);
end $$;
revoke all on function god_mode_ops.lee_comp_usable(jsonb,jsonb),god_mode_ops.refresh_lee_market_evidence(integer) from public,anon,authenticated;
grant execute on function god_mode_ops.lee_comp_usable(jsonb,jsonb),god_mode_ops.refresh_lee_market_evidence(integer) to service_role;
