do $$
begin
  assert god_mode_ops.gis_polygon_valid('{"rings":[[[0,0],[1,0],[1,1],[0,0]]]}');
  assert not god_mode_ops.gis_polygon_valid(null);
  assert not god_mode_ops.gis_polygon_valid('{"rings":[]}');
  assert not god_mode_ops.gis_polygon_valid('{"rings":[[[0,0],[1,0],[1,1],[0,1]]]}');
  assert not god_mode_ops.gis_polygon_valid('{"rings":[[[0,0],[181,0],[1,1],[0,0]]]}');
  assert not god_mode_ops.gis_polygon_valid('{"rings":[[[0,0],["1",0],[1,1],[0,0]]]}');
  assert not has_function_privilege('anon','god_mode_ops.run_lee_gis_queue(integer,boolean)','EXECUTE');
  assert not has_function_privilege('authenticated','god_mode_ops.run_lee_gis_queue(integer,boolean)','EXECUTE');
end $$;
