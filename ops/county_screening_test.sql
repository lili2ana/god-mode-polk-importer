do $$ declare c jsonb;
begin
  c := god_mode_ops.county_gis_config('Charlotte');
  assert c->>'identity_field'='ACCOUNT';
  assert c->>'parcel_endpoint' like '%CCGIS_BaseLayers/MapServer/5/query';
  assert god_mode_ops.road_surface_class('charlotte','{"PAVED":" YES "}')='PAVED';
  assert god_mode_ops.road_surface_class('charlotte','{"PAVED":null}')='REVIEW';
  assert god_mode_ops.road_surface_class('lee','{"SURFACE":"DIRT"}')='UNPAVED';
  assert god_mode_ops.road_surface_class('marion','{"Paved":0,"Jurisdiction":"City"}')='REVIEW';
  assert god_mode_ops.road_surface_class('highlands','{"MAINT_CAT":41}')='PAVED';
  assert god_mode_ops.road_surface_class('highlands','{"MAINT_CAT":5}')='REVIEW';
  assert god_mode_ops.county_gis_config('unknown') is null;
  assert god_mode_ops.county_gis_config('sarasota')->>'identity_field'='ID';
  assert god_mode_ops.road_surface_class('sarasota','{"SurfaceType":"Asphaltic Concrete"}')='PAVED';
end $$;
