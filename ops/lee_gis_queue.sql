-- Fetch boundaries per pending parcel instead of relying on old cached evidence.
create or replace function god_mode_ops.gis_polygon_valid(g jsonb)
returns boolean language plpgsql immutable set search_path = '' as $$
declare ring jsonb; point jsonb;
begin
  if jsonb_typeof(g->'rings') is distinct from 'array' or jsonb_array_length(g->'rings')=0 then return false; end if;
  for ring in select value from jsonb_array_elements(g->'rings') loop
    if jsonb_typeof(ring) is distinct from 'array' or jsonb_array_length(ring)<4
      or ring->0 <> ring->(jsonb_array_length(ring)-1) then return false; end if;
    for point in select value from jsonb_array_elements(ring) loop
      if jsonb_typeof(point) is distinct from 'array' or jsonb_array_length(point)<2
        or jsonb_typeof(point->0) is distinct from 'number'
        or jsonb_typeof(point->1) is distinct from 'number'
        or (point->>0)::numeric not between -180 and 180
        or (point->>1)::numeric not between -90 and 90 then return false; end if;
    end loop;
  end loop;
  return true;
exception when others then return false;
end $$;

create or replace function god_mode_ops.run_lee_gis_queue(p_limit integer default 2, p_force boolean default false)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r record; screen record; query jsonb; reply jsonb; geometry jsonb; features jsonb;
  details jsonb; outcome text; done integer := 0; run_at timestamptz := clock_timestamp();
  parcel_endpoint constant text := 'https://gismapserver.leegov.com/gisserver910/rest/services/DataExplorer/Parcels/MapServer/0/query';
begin
  if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
  if not pg_try_advisory_xact_lock(714426001) then return '{"processed":0,"busy":true}'; end if;
  for r in
    select s.parcel_id from god_mode_ops.buyer_demand_land_screening s
    left join lateral (
      select max(e.checked_at) last_checked from god_mode_ops.gis_worker_evidence e
      where e.parcel_id=s.parcel_id and e.details->>'worker'='lee_queue_v1'
    ) last_run on true
    where lower(s.county)='lee' and s.screening_status='PENDING_FLOOD_WETLAND_ROAD_DISTRESS'
      and (p_force or last_run.last_checked is null or last_run.last_checked < run_at - interval '20 hours')
    order by last_run.last_checked nulls first, s.parcel_id
    limit least(greatest(coalesce(p_limit,2),1),5)
  loop
    geometry := null;
    reply := god_mode_ops.gis_query(parcel_endpoint,jsonb_build_object(
      'where',format('STRAP = %L',r.parcel_id),'outFields','OBJECTID,STRAP,FOLIOID',
      'returnGeometry','true','outSR','4326'));
    if reply->>'ok'='true' and reply->'data'->'spatialReference'->>'wkid'='4326'
      and jsonb_array_length(reply->'data'->'features')=1
      and reply->'data'->'features'->0->'attributes'->>'STRAP'=r.parcel_id then
      geometry := jsonb_build_object('rings',reply->'data'->'features'->0->'geometry'->'rings',
        'spatialReference',jsonb_build_object('wkid',4326));
      if not god_mode_ops.gis_polygon_valid(geometry) then geometry := null; end if;
    end if;
    insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
    values(r.parcel_id,'LEE_PARCEL_GEOMETRY',parcel_endpoint,
      case when geometry is null then 'ERROR' else 'FOUND' end,
      jsonb_build_object('worker','lee_queue_v1','checked_at',clock_timestamp(),
        'polygon_geometry',geometry,'numeric_parcel_objectid',reply->'data'->'features'->0->'attributes'->'OBJECTID',
        'folioid',reply->'data'->'features'->0->'attributes'->'FOLIOID',
        'request',reply-'data','identity_verified',geometry is not null));
    if geometry is null then done := done+1; continue; end if;
    for screen in select * from (values
      ('FEMA_FLOOD','https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query','FLD_ZONE,ZONE_SUBTY,SFHA_TF',false),
      ('USFWS_WETLANDS','https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query','ATTRIBUTE,WETLAND_TYPE',false),
      ('LEE_ROAD','https://gismapserver.leegov.com/gisserver910/rest/services/Layers/RoadCenterlines/MapServer/2/query','OBJECTID,STREET,SURFACE,MAINTAINBY,ROW_WIDTH',true)
    ) v(source,endpoint,fields,road) loop
      query := jsonb_build_object('geometry',geometry::text,'geometryType','esriGeometryPolygon',
        'inSR','4326','outSR','4326','spatialRel','esriSpatialRelIntersects',
        'returnGeometry',case when screen.road then 'true' else 'false' end,'outFields',screen.fields);
      if screen.road then query := query || '{"distance":"30","units":"esriSRUnit_Meter"}'; end if;
      reply := god_mode_ops.gis_query(screen.endpoint,query);
      features := case when reply->>'ok'='true' then reply->'data'->'features' else null end;
      outcome := case when features is null then 'ERROR'
        when jsonb_array_length(features)>0 then 'FOUND'
        when screen.source='FEMA_FLOOD' then 'UNVERIFIED' else 'NOT_FOUND' end;
      details := jsonb_build_object('worker','lee_queue_v1','checked_at',clock_timestamp(),
        'method',case when screen.road then '30m_full_polygon_buffer_not_verified_frontage' else 'full_parcel_polygon_intersection' end,
        'feature_count',jsonb_array_length(features),'features',features,'request',reply-'data',
        'screening_clearance',false,'frontage_verified',false,'legal_access_verified',false,
        'mapped_wetland_absence_not_regulatory_clearance',true);
      if screen.road then details := details || jsonb_build_object('paved_candidate_count',
        (select count(*) from jsonb_array_elements(features) f where upper(trim(f->'attributes'->>'SURFACE'))='PAVED')); end if;
      insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
      values(r.parcel_id,screen.source,screen.endpoint,outcome,details);
    end loop;
    -- A GIS refresh does not clear frontage, title, distress, or the contact gate.
    update god_mode_ops.buyer_demand_land_screening set updated_at=clock_timestamp()
    where parcel_id=r.parcel_id;
    done := done+1;
  end loop;
  return jsonb_build_object('processed',done,'worker','lee_queue_v1','started_at',run_at);
end $$;
revoke all on function god_mode_ops.gis_polygon_valid(jsonb), god_mode_ops.run_lee_gis_queue(integer,boolean) from public,anon,authenticated;
grant execute on function god_mode_ops.gis_polygon_valid(jsonb), god_mode_ops.run_lee_gis_queue(integer,boolean) to service_role;
