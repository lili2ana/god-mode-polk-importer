-- A centerline crossing the interior must not become legal frontage.
do $$ declare g jsonb := '{"rings":[[[-81.71,26.60],[-81.71,26.601],[-81.709,26.601],[-81.709,26.60],[-81.71,26.60]]]}'; v jsonb;
begin
  v := god_mode_ops.frontage_metrics(g,'[{"geometry":{"paths":[[[-81.71,26.60],[-81.71,26.601]]]},"attributes":{"OBJECTID":1}}]');
  assert (v->>'mapped_overlap_m')::numeric > 100;
  assert v->>'exact_frontage_verified'='false';
  assert v->>'legal_access_verified'='false';
  v := god_mode_ops.frontage_metrics(g,'[{"geometry":{"paths":[[[-81.7095,26.599],[-81.7095,26.602]]]},"attributes":{"OBJECTID":2}}]');
  assert (v->>'mapped_overlap_m')::numeric < 2;
  v := god_mode_ops.frontage_metrics(g,'[]');
  assert v->>'status'='REVIEW_NO_ROW_GEOMETRY';
  assert v->>'exact_frontage_verified'='false';
  begin
    perform god_mode_ops.frontage_metrics('{"rings":[]}','[]');
    raise exception 'invalid polygon accepted';
  exception when raise_exception then
    if sqlerrm <> 'invalid parcel geometry' then raise; end if;
  end;
end $$;
