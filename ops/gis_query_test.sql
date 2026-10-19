do $$
begin
  assert god_mode_ops.gis_response_complete('{"features":[]}');
  assert not god_mode_ops.gis_response_complete(null);
  assert not god_mode_ops.gis_response_complete('{}');
  assert not god_mode_ops.gis_response_complete('{"error":{},"features":[]}');
  assert not god_mode_ops.gis_response_complete('{"features":[],"exceededTransferLimit":true}');
  assert not god_mode_ops.gis_response_complete('{"features":null}');
  assert not has_function_privilege('anon','god_mode_ops.gis_query(text,jsonb)','EXECUTE');
  assert not has_function_privilege('authenticated','god_mode_ops.gis_query(text,jsonb)','EXECUTE');
  begin
    perform god_mode_ops.gis_query('https://example.com/query','{}');
    raise exception 'endpoint guard failed';
  exception when raise_exception then
    if sqlerrm <> 'invalid GIS request' then raise; end if;
  end;
end $$;
