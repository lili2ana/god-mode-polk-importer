do $$
begin
  assert not god_mode_ops.county_preflight_pass('{}','lee','subject',0.25);
  assert not god_mode_ops.county_preflight_pass('{"zoning_verified":true,"source_verified":true,"county":"polk","sample":[]}','lee','subject',0.25);
  assert god_mode_ops.county_preflight_pass(jsonb_build_object('county','lee','zoning_verified',true,
    'source_verified',true,'checked_at',now(),'sample',jsonb_build_array(
      jsonb_build_object('parcel_id','comp1','county','lee','asset_type','land','acreage',0.25,'price',20000,'sale_date',current_date,'verified',true),
      jsonb_build_object('parcel_id','comp2','county','lee','asset_type','land','acreage',0.25,'price',25000,'sale_date',current_date,'verified',true),
      jsonb_build_object('parcel_id','comp3','county','lee','asset_type','land','acreage',0.25,'price',30000,'sale_date',current_date,'verified',true)
    )),'lee','subject',0.25);
end $$;
