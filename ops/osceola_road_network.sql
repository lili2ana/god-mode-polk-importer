-- County-hosted geometry plus St. Cloud JPA maintained-network overlay.
-- JPA owner classifications include private roads; neither source proves pavement.
create or replace function god_mode_ops.osceola_road_network_check(p_parcel text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare cfg jsonb; p jsonb; r jsonb; m jsonb; g jsonb; params jsonb;
begin
 if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
 cfg:=god_mode_ops.county_gis_config('osceola');
 p:=god_mode_ops.gis_query(cfg->>'parcel_endpoint',jsonb_build_object('where',format('PARCELID=%L',p_parcel),
  'outFields','OBJECTID,PARCELID','returnGeometry','true','outSR','4326'));
 if p->'ok' is distinct from 'true'::jsonb or jsonb_array_length(p->'data'->'features')<>1
  or p->'data'->'features'->0->'attributes'->>'PARCELID' is distinct from p_parcel then
  return '{"parcel_verified":false,"pavement_status":"REVIEW_REQUIRED","legal_access_verified":false}'; end if;
 g:=p->'data'->'features'->0->'geometry';
 if not god_mode_ops.gis_polygon_valid(g) then return '{"parcel_verified":false,"pavement_status":"REVIEW_REQUIRED","legal_access_verified":false}'; end if;
 params:=jsonb_build_object('geometry',g::text,'geometryType','esriGeometryPolygon','inSR','4326','outSR','4326',
  'spatialRel','esriSpatialRelIntersects','distance',30,'units','esriSRUnit_Meter','returnGeometry','true');
 r:=god_mode_ops.gis_query(cfg->>'road_endpoint',params||jsonb_build_object('outFields','OBJECTID,STREET'));
 m:=god_mode_ops.gis_query('https://services1.arcgis.com/9AYCAcYVeEGk3ZMt/arcgis/rest/services/Maintained_Roadways/FeatureServer/1/query',
  params||jsonb_build_object('outFields','OBJECTID_1,STREET,owner'));
 return jsonb_build_object('parcel_verified',true,'road_query_ok',r->'ok','road_count',jsonb_array_length(r->'data'->'features'),
  'road_source',cfg->>'road_endpoint','maintained_network_query_ok',m->'ok',
  'maintained_network_features',m->'data'->'features','maintained_network_count',jsonb_array_length(m->'data'->'features'),
  'maintenance_scope','St. Cloud JPA only; includes private maintenance; source snapshot not current maintenance approval',
  'method','30m full parcel polygon spatial overlay; no legal frontage inference',
  'pavement_status','REVIEW_REQUIRED','maintained_right_of_way_verified',false,'legal_access_verified',false,'checked_at',now());
end $$;
revoke all on function god_mode_ops.osceola_road_network_check(text) from public,anon,authenticated;
grant execute on function god_mode_ops.osceola_road_network_check(text) to service_role;
