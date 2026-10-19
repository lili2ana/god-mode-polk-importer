-- PostGIS 3.3.7: https://postgis.net/docs/ST_BuildArea.html and ST_Transform.html
create extension if not exists postgis with schema extensions;
create or replace function god_mode_ops.frontage_metrics(g jsonb, row_features jsonb)
returns jsonb language plpgsql immutable security invoker set search_path = '' as $$
declare parcel extensions.geometry; lines extensions.geometry; clipped extensions.geometry;
  measured numeric; nearest numeric;
begin
  if not god_mode_ops.gis_polygon_valid(g) then raise exception 'invalid parcel geometry'; end if;
  parcel := extensions.st_buildarea(extensions.st_geomfromgeojson(jsonb_build_object(
    'type','MultiLineString','coordinates',g->'rings')));
  if parcel is null or not extensions.st_isvalid(parcel) then raise exception 'invalid parcel geometry'; end if;
  parcel := extensions.st_transform(extensions.st_setsrid(parcel,4326),26917);
  if jsonb_typeof(row_features) is distinct from 'array' then raise exception 'invalid ROW response'; end if;
  if exists(select 1 from jsonb_array_elements(row_features) f
    where jsonb_typeof(f->'geometry'->'paths') is distinct from 'array') then
    raise exception 'invalid ROW geometry';
  end if;
  select extensions.st_unaryunion(extensions.st_collect(extensions.st_transform(
    extensions.st_setsrid(extensions.st_geomfromgeojson(jsonb_build_object(
      'type','MultiLineString','coordinates',f->'geometry'->'paths')),4326),26917)))
  into lines from jsonb_array_elements(row_features) f;
  if lines is null or extensions.st_isempty(lines) then
    return jsonb_build_object('status','REVIEW_NO_ROW_GEOMETRY','exact_frontage_verified',false,'legal_access_verified',false);
  end if;
  -- A 0.20m matching tolerance deals only with mapped line alignment, not survey accuracy.
  clipped := extensions.st_intersection(extensions.st_boundary(parcel),extensions.st_buffer(lines,0.20));
  measured := extensions.st_length(clipped); nearest := extensions.st_distance(extensions.st_boundary(parcel),lines);
  return jsonb_build_object('status','REVIEW_RECORDED_DOCUMENTS','mapped_overlap_m',round(measured,3),
    'mapped_overlap_ft',round(measured/0.3048,2),'nearest_row_distance_m',round(nearest,3),
    'matching_tolerance_m',0.20,'measurement_crs',26917,'exact_frontage_verified',false,
    'legal_access_verified',false,'method','parcel_boundary_overlap_with_mapped_row_not_recorded_frontage');
end $$;
revoke all on function god_mode_ops.frontage_metrics(jsonb,jsonb) from public,anon,authenticated;
grant execute on function god_mode_ops.frontage_metrics(jsonb,jsonb) to service_role;
