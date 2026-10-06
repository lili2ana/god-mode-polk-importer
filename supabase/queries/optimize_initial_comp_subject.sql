CREATE OR REPLACE FUNCTION public.god_mode_initial_comp_candidates(p_parcel text)
 RETURNS jsonb
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
WITH subject AS MATERIALIZED (
 SELECT p.* FROM public.polk_parcel_v2 p
 WHERE p.parcel_id=p_parcel AND p_parcel ~ '^[0-9]{18}$'
 AND EXISTS(SELECT 1 FROM public.properties target WHERE target.parcel_id=p_parcel
 AND god_mode_ops.seller_dd_eligible(target.id))
), candidates AS (
 SELECT DISTINCT ON(s.parcel_id) s.parcel_id,s.saledt,s.price,p.dordesc1,
 CASE WHEN pg_input_is_valid(p.tot_acreage,'numeric') THEN p.tot_acreage::numeric END AS acres,
 subj.tot_acreage AS subject_acres
 FROM public.polk_sales_recent_shadow s JOIN public.polk_parcel_v2 p ON p.parcel_id=s.parcel_id
 JOIN subject subj ON p.nh_cd=subj.nh_cd AND p.dorus_code=subj.dorus_code
 WHERE s.parcel_id<>subj.parcel_id AND s.saledt BETWEEN (current_date-interval '2 years')::date AND current_date
 AND s.price>10000 AND btrim(s.foreclosure)='N' AND btrim(s.trns_cd)='W'
 AND CASE WHEN pg_input_is_valid(p.tot_acreage,'numeric') AND pg_input_is_valid(subj.tot_acreage,'numeric')
 THEN p.tot_acreage::numeric BETWEEN subj.tot_acreage::numeric*0.5 AND subj.tot_acreage::numeric*2 ELSE false END
 ORDER BY s.parcel_id,s.saledt DESC,s.ln_num DESC
), selected AS (SELECT * FROM candidates ORDER BY saledt DESC,parcel_id LIMIT 10)
SELECT jsonb_build_object('sample',coalesce(jsonb_agg(jsonb_build_object(
 'PARNO',parcel_id,'PARUSEDESC',dordesc1,'SALE1_DATE',saledt,'SALE1_AMT',price,'ACRES',acres) ORDER BY saledt DESC),'[]'::jsonb),
 'source','Polk recent-sales shadow / same assessor neighborhood and use code',
 'source_coverage','partial_staging_subset','scope','initial_screen_only_not_ARV',
 'count',count(*),'source_error',false,'qualification','not_verified')
FROM selected;
$function$
