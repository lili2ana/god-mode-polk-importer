CREATE OR REPLACE FUNCTION god_mode_ops.rough_sales_screen_pass(p_comps jsonb,p_subject text)
RETURNS boolean LANGUAGE sql STABLE SET search_path='' AS $$
WITH candidates AS (
 SELECT regexp_replace(coalesce(c->>'PARNO',''),'[^0-9]','','g') AS parcel,
 CASE WHEN (c->>'SALE1_AMT') ~ '^[0-9]+([.][0-9]+)?$' THEN (c->>'SALE1_AMT')::numeric ELSE NULL END AS price,
 CASE WHEN pg_input_is_valid(c->>'SALE1_DATE','date') THEN (c->>'SALE1_DATE')::date ELSE NULL END AS sale_date
 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p_comps->'sample')='array' THEN p_comps->'sample' ELSE '[]'::jsonb END) c
) SELECT count(distinct parcel)>=3 FROM candidates
 WHERE parcel ~ '^[0-9]{18}$' AND parcel<>p_subject AND price>0
 AND sale_date BETWEEN (current_date-interval '5 years')::date AND current_date;
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
   AND god_mode_ops.rough_sales_screen_pass(d.findings->'comps',p.parcel_id),false),
 'no_fatal_flags',CASE WHEN jsonb_typeof(d.fatal_flags)='array'
   THEN jsonb_array_length(d.fatal_flags)=0 ELSE false END)
 FROM public.due_diligence_reviews d JOIN public.properties p ON p.id=d.property_id
 WHERE d.property_id=p_property_id AND d.status IN ('review_required','machine_complete','completed')
 ORDER BY d.updated_at DESC LIMIT 1),'{}'::jsonb);
$$;

REVOKE ALL ON FUNCTION god_mode_ops.rough_sales_screen_pass(jsonb,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION god_mode_ops.rough_sales_screen_pass(jsonb,text) TO service_role;
DO $test$
DECLARE sample jsonb;
BEGIN
 SELECT jsonb_build_object('sample',jsonb_agg(jsonb_build_object('PARNO',lpad(n::text,18,'0'),'SALE1_AMT',10000,'SALE1_DATE',current_date-30))) INTO sample FROM generate_series(1,3)n;
 IF NOT god_mode_ops.rough_sales_screen_pass(sample,'999999999999999999') THEN RAISE EXCEPTION 'Recent sample rejected'; END IF;
 IF god_mode_ops.rough_sales_screen_pass(sample,'000000000000000001')
 OR god_mode_ops.rough_sales_screen_pass('{}','999999999999999999') THEN RAISE EXCEPTION 'Insufficient sample accepted'; END IF;
 SELECT jsonb_build_object('sample',jsonb_agg(jsonb_build_object('PARNO',lpad(n::text,18,'0'),'SALE1_AMT',10000,'SALE1_DATE',current_date-2500))) INTO sample FROM generate_series(1,3)n;
 IF god_mode_ops.rough_sales_screen_pass(sample,'999999999999999999') THEN RAISE EXCEPTION 'Stale sales accepted'; END IF;
END $test$;
