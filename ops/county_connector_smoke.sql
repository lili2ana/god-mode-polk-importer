-- Diagnostic samples are not acquisition leads or countywide coverage proof.
do $$ declare c text; cfg jsonb; response extensions.http_response; sample text;
  parcel jsonb; road jsonb; geom jsonb; results jsonb := '[]';
begin
  perform set_config('http.curlopt_timeout_ms','8000',true);
  perform set_config('http.curlopt_connecttimeout_ms','3000',true);
  foreach c in array array['lee','charlotte','marion','highlands','polk','sarasota','osceola'] loop
    cfg := god_mode_ops.county_gis_config(c);
    begin
      -- Pagination here discovers one ID only; the exact-ID response below
      -- must pass the complete-response guard before spatial querying.
      response := extensions.http_get(cfg->>'parcel_endpoint',jsonb_build_object(
        'where','1=1','outFields',cfg->>'identity_field','returnGeometry','false','resultRecordCount','1','f','json'));
      sample := response.content::jsonb->'features'->0->'attributes'->>(cfg->>'identity_field');
      if response.status<>200 or sample is null then raise exception 'sample unavailable'; end if;
      parcel := god_mode_ops.gis_query(cfg->>'parcel_endpoint',jsonb_build_object(
        'where',format('%s = %L',cfg->>'identity_field',sample),'outFields',cfg->>'parcel_fields',
        'returnGeometry','true','outSR','4326'));
      if parcel->>'ok' is distinct from 'true' or jsonb_array_length(parcel->'data'->'features')<>1
        or parcel->'data'->'features'->0->'attributes'->>(cfg->>'identity_field') is distinct from sample
      then raise exception 'identity or geometry unavailable'; end if;
      geom := jsonb_build_object('rings',parcel->'data'->'features'->0->'geometry'->'rings');
      if not god_mode_ops.gis_polygon_valid(geom) then raise exception 'invalid polygon'; end if;
      road := god_mode_ops.gis_query(cfg->>'road_endpoint',jsonb_build_object('geometry',geom::text,
        'geometryType','esriGeometryPolygon','inSR','4326','outSR','4326','distance','30',
        'units','esriSRUnit_Meter','spatialRel','esriSpatialRelIntersects','returnGeometry','true','outFields',cfg->>'road_fields'));
      results := results || jsonb_build_array(jsonb_build_object('county',c,'parcel_verified',true,
        'road_query_ok',road->'ok','road_count',jsonb_array_length(road->'data'->'features'),
        'road_diagnostic',road-'data','coverage_verified',false));
    exception when others then
      results := results || jsonb_build_array(jsonb_build_object('county',c,'error',sqlerrm,'coverage_verified',false));
    end;
  end loop;
  perform set_config('god_mode.connector_test_result',results::text,true);
end $$;
select current_setting('god_mode.connector_test_result')::jsonb as connector_results;
