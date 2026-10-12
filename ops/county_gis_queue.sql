-- Requires county_sources.sql, gis_query.sql, lee_gis_queue.sql and frontage.sql.
create or replace function god_mode_ops.run_county_gis_queue(p_county text default null, p_limit integer default 2, p_force boolean default false)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r record; screen record; query jsonb; reply jsonb; geometry jsonb; features jsonb;
  details jsonb; cfg jsonb; row_reply jsonb; outcome text; done integer := 0; run_at timestamptz := clock_timestamp();
  parcel_endpoint text;
begin
  if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
  if not pg_try_advisory_xact_lock(714426002) then return '{"processed":0,"busy":true}'; end if;
  for r in
    select s.parcel_id,s.county from god_mode_ops.buyer_demand_land_screening s
    left join lateral (
      select max(e.checked_at) last_checked from god_mode_ops.gis_worker_evidence e
      where e.parcel_id=s.parcel_id and e.details->>'worker'='county_queue_v1'
    ) last_run on true
    where (p_county is null or lower(s.county)=lower(p_county)) and s.screening_status='PENDING_FLOOD_WETLAND_ROAD_DISTRESS'
      and (p_force or last_run.last_checked is null or last_run.last_checked < run_at - interval '20 hours')
    order by last_run.last_checked nulls first, s.parcel_id
    limit least(greatest(coalesce(p_limit,2),1),5)
  loop
    geometry := null; cfg := god_mode_ops.county_gis_config(r.county);
    if cfg is null then
      insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
      values(r.parcel_id,'COUNTY_SOURCE_CONFIGURATION','internal:county_gis_config','ERROR',jsonb_build_object('worker','county_queue_v1','kind','unsupported_county'));
      done:=done+1; continue;
    end if;
    parcel_endpoint := cfg->>'parcel_endpoint';
    reply := god_mode_ops.gis_query(parcel_endpoint,jsonb_build_object(
      'where',format('%s = %L',cfg->>'identity_field',r.parcel_id),'outFields',cfg->>'parcel_fields',
      'returnGeometry','true','outSR','4326'));
    if reply->>'ok'='true' and reply->'data'->'spatialReference'->>'wkid'='4326'
      and jsonb_array_length(reply->'data'->'features')=1
      and reply->'data'->'features'->0->'attributes'->>(cfg->>'identity_field')=r.parcel_id then
      geometry := jsonb_build_object('rings',reply->'data'->'features'->0->'geometry'->'rings',
        'spatialReference',jsonb_build_object('wkid',4326));
      if not god_mode_ops.gis_polygon_valid(geometry) then geometry := null; end if;
    end if;
    insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
    values(r.parcel_id,upper(r.county)||'_PARCEL_GEOMETRY',parcel_endpoint,
      case when geometry is null then 'ERROR' else 'FOUND' end,
      jsonb_build_object('worker','county_queue_v1','checked_at',clock_timestamp(),
        'polygon_geometry',geometry,'numeric_parcel_objectid',reply->'data'->'features'->0->'attributes'->'OBJECTID',
        'parcel_attributes',reply->'data'->'features'->0->'attributes',
        'request',reply-'data','identity_verified',geometry is not null));
    if geometry is null then done := done+1; continue; end if;
    for screen in select * from (values
      ('FEMA_FLOOD','https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query','FLD_ZONE,ZONE_SUBTY,SFHA_TF',false),
      ('USFWS_WETLANDS','https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query','ATTRIBUTE,WETLAND_TYPE',false),
      (upper(r.county)||'_ROAD',cfg->>'road_endpoint',cfg->>'road_fields',true)
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
      details := jsonb_build_object('worker','county_queue_v1','checked_at',clock_timestamp(),
        'method',case when screen.road then '30m_full_polygon_buffer_not_verified_frontage' else 'full_parcel_polygon_intersection' end,
        'feature_count',jsonb_array_length(features),'features',features,'request',reply-'data',
        'screening_clearance',false,'frontage_verified',false,'legal_access_verified',false,
        'mapped_wetland_absence_not_regulatory_clearance',true);
      if screen.road then details := details || jsonb_build_object('paved_candidate_count',
        (select count(*) from jsonb_array_elements(features) f where god_mode_ops.road_surface_class(r.county,f->'attributes')='PAVED')); end if;
      insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
      values(r.parcel_id,screen.source,screen.endpoint,outcome,details);
    end loop;
    if lower(r.county)='osceola' then
      details:=god_mode_ops.osceola_road_network_check(r.parcel_id);
      insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
      values(r.parcel_id,'OSCEOLA_MAINTAINED_NETWORK',cfg->>'road_endpoint','UNVERIFIED',
        details||jsonb_build_object('worker','county_queue_v1'));
    end if;
    if cfg->>'row_endpoint' is not null then
      query := jsonb_build_object('geometry',geometry::text,'geometryType','esriGeometryPolygon',
        'inSR','4326','outSR','4326','spatialRel','esriSpatialRelIntersects','distance','3',
        'units','esriSRUnit_Meter','outFields','OBJECTID_1,PROJ_NAME,OR_BOOK_PG,HOTLINK,DrawingLocation','returnGeometry','true');
      row_reply := god_mode_ops.gis_query(cfg->>'row_endpoint',query);
      if row_reply->>'ok'='true' then
        begin
          details := god_mode_ops.frontage_metrics(geometry,row_reply->'data'->'features');
        exception when others then details := '{"status":"REVIEW_INVALID_ROW_GEOMETRY"}'; end;
      else details := '{"status":"REVIEW_ROW_SOURCE_ERROR"}'; end if;
      details := details || jsonb_build_object('request',row_reply-'data','row_features',row_reply->'data'->'features');
    else details := '{"status":"REVIEW_NO_VERIFIED_ROW_SOURCE"}'; end if;
    insert into god_mode_ops.gis_worker_evidence(parcel_id,source,source_url,result,details)
    values(r.parcel_id,'FRONTAGE_ACCESS',coalesce(cfg->>'row_endpoint','internal:county_gis_config'),'UNVERIFIED',
      details || jsonb_build_object('worker','county_queue_v1','exact_frontage_verified',false,'legal_access_verified',false));
    -- A GIS refresh does not clear frontage, title, distress, or the contact gate.
    update god_mode_ops.buyer_demand_land_screening set updated_at=clock_timestamp()
    where parcel_id=r.parcel_id;
    done := done+1;
  end loop;
  return jsonb_build_object('processed',done,'worker','county_queue_v1','started_at',run_at);
end $$;
revoke all on function god_mode_ops.run_county_gis_queue(text,integer,boolean) from public,anon,authenticated;
grant execute on function god_mode_ops.run_county_gis_queue(text,integer,boolean) to service_role;
