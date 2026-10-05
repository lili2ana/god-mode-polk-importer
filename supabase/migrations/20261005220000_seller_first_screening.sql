-- Seller-first order: light screen for enrichment, full DD reserved for transactions.
CREATE OR REPLACE FUNCTION god_mode_ops.seller_deep_dd_eligible(p_property_id uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
 select god_mode_ops.seller_dd_eligible(p_property_id) and exists(
 select 1 from public.due_diligence_reviews d where d.property_id=p_property_id
 and d.status='completed'
 and d.title_status in ('verified','passed')
 and d.tax_status in ('verified','passed')
 and d.zoning_status in ('verified','passed')
 and d.access_status in ('verified','passed')
 and d.utilities_status in ('verified','passed')
 and d.flood_status in ('verified','passed')
 and d.wetlands_status in ('verified','passed')
 and d.comps_status in ('qualified','verified','passed')
 and d.exit_status in ('verified','passed','buyer_path_verified')
 and d.underwriting_status in ('qualified','approved','passed')
 and jsonb_typeof(d.fatal_flags)='array' and jsonb_array_length(d.fatal_flags)=0
 and d.completed_at>=now()-interval '7 days');
$function$

CREATE OR REPLACE FUNCTION god_mode_ops.screen_check_fresh(p_check jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path='' AS $$
 SELECT CASE WHEN pg_input_is_valid(p_check->>'checked_at','timestamp with time zone')
 THEN (p_check->>'checked_at')::timestamptz BETWEEN now()-interval '7 days' AND now()
 ELSE false END;
$$;
CREATE OR REPLACE FUNCTION god_mode_ops.initial_screen_checks_pass(p_checks jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path='' AS $$
 SELECT coalesce(jsonb_typeof(p_checks)='object' AND
 p_checks->'parcel_size'='true'::jsonb AND
 p_checks->'zoning_screen'='true'::jsonb AND
 p_checks->'flood_screen'='true'::jsonb AND
 p_checks->'wetlands_screen'='true'::jsonb AND
 p_checks->'access_screen'='true'::jsonb AND
 p_checks->'rough_comps'='true'::jsonb AND
 p_checks->'no_fatal_flags'='true'::jsonb,false);
$$;
CREATE OR REPLACE FUNCTION god_mode_ops.seller_initial_screen_checks(p_property_id uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path='' AS $$
 SELECT coalesce((
 SELECT jsonb_build_object(
 'parcel_size',coalesce(p.acreage>0,false),
 'zoning_screen',coalesce(d.zoning_status IN ('verified_gis','future_land_use_verified','verified','passed')
   AND god_mode_ops.screen_check_fresh(d.findings->'zoning')
   AND jsonb_typeof(d.findings#>'{zoning,attributes}')='object'
   AND upper(coalesce(d.findings#>>'{zoning,classification}',d.findings#>>'{zoning,attributes,FLUNAME}',''))<>'CITY',false),
 'flood_screen',coalesce(god_mode_ops.screen_check_fresh(d.findings->'flood')
   AND d.findings#>'{flood,source_error}'='false'::jsonb
   AND nullif(d.findings#>>'{flood,zone}','') IS NOT NULL,false),
 'wetlands_screen',coalesce(god_mode_ops.screen_check_fresh(d.findings->'wetlands')
   AND d.findings#>'{wetlands,hit}'='false'::jsonb
   AND d.findings#>'{wetlands,source_error}'='false'::jsonb,false),
 'access_screen',coalesce(d.access_status IN ('verified_near_mapped_street','mapped_road_proximity_verified','verified','passed')
   AND god_mode_ops.screen_check_fresh(d.findings->'access')
   AND (jsonb_typeof(d.findings#>'{access,nearby_street}')='object'
     OR jsonb_typeof(d.findings#>'{access,nearby_road}')='object'),false),
 'rough_comps',coalesce(god_mode_ops.screen_check_fresh(d.findings->'comps')
   AND d.findings#>'{comps,source_error}'='false'::jsonb
   AND CASE WHEN jsonb_typeof(d.findings#>'{comps,unqualified_candidate_median}')='number'
    THEN (d.findings#>>'{comps,unqualified_candidate_median}')::numeric>0 ELSE false END
   AND CASE WHEN jsonb_typeof(d.findings#>'{comps,count}')='number'
    THEN (d.findings#>>'{comps,count}')::numeric>=3 ELSE false END,false),
 'no_fatal_flags',CASE WHEN jsonb_typeof(d.fatal_flags)='array'
   THEN jsonb_array_length(d.fatal_flags)=0 ELSE false END)
 FROM public.due_diligence_reviews d JOIN public.properties p ON p.id=d.property_id
 WHERE d.property_id=p_property_id AND d.status IN ('review_required','machine_complete','completed')
 ORDER BY d.updated_at DESC LIMIT 1),'{}'::jsonb);
$$;
CREATE OR REPLACE FUNCTION god_mode_ops.seller_enrichment_eligible(p_property_id uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path='' AS $$
 SELECT god_mode_ops.seller_dd_eligible(p_property_id)
 AND god_mode_ops.initial_screen_checks_pass(god_mode_ops.seller_initial_screen_checks(p_property_id));
$$;
COMMENT ON FUNCTION god_mode_ops.seller_enrichment_eligible(uuid) IS
 'Fresh verified distress plus initial screening only. No full DD, title, exact underwriting or confirmed buyer required for contact enrichment. Does not authorize messages or contracts.';
COMMENT ON FUNCTION god_mode_ops.seller_deep_dd_eligible(uuid) IS
 'Preserved full DD rule for later transaction evaluation; initial screening never grants transaction release.';
REVOKE ALL ON FUNCTION god_mode_ops.screen_check_fresh(jsonb),god_mode_ops.initial_screen_checks_pass(jsonb),god_mode_ops.seller_initial_screen_checks(uuid),god_mode_ops.seller_deep_dd_eligible(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION god_mode_ops.screen_check_fresh(jsonb),god_mode_ops.initial_screen_checks_pass(jsonb),god_mode_ops.seller_initial_screen_checks(uuid),god_mode_ops.seller_deep_dd_eligible(uuid) TO service_role;
DO $test$
DECLARE checks jsonb='{"parcel_size":true,"zoning_screen":true,"flood_screen":true,"wetlands_screen":true,"access_screen":true,"rough_comps":true,"no_fatal_flags":true}'; k text;
BEGIN
 IF NOT god_mode_ops.initial_screen_checks_pass(checks) THEN RAISE EXCEPTION 'Complete initial screen rejected'; END IF;
 FOREACH k IN ARRAY ARRAY['parcel_size','zoning_screen','flood_screen','wetlands_screen','access_screen','rough_comps','no_fatal_flags'] LOOP
  IF god_mode_ops.initial_screen_checks_pass(checks-k)
   OR god_mode_ops.initial_screen_checks_pass(jsonb_set(checks,ARRAY[k],'false'))
   OR god_mode_ops.initial_screen_checks_pass(jsonb_set(checks,ARRAY[k],'"true"')) THEN
   RAISE EXCEPTION 'Missing/failed/malformed screening check accepted: %',k; END IF;
 END LOOP;
 IF god_mode_ops.initial_screen_checks_pass(NULL) OR god_mode_ops.screen_check_fresh('{"checked_at":"bad"}') OR god_mode_ops.screen_check_fresh(jsonb_build_object('checked_at',now()-interval '8 days'))
 THEN RAISE EXCEPTION 'Unknown or stale evidence accepted'; END IF;
END $test$;
