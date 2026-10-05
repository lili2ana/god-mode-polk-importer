BEGIN;
DO $test$
DECLARE target uuid; sample jsonb; stamp timestamptz=now();
BEGIN
 SELECT p.id INTO STRICT target FROM public.properties p
 WHERE god_mode_ops.seller_dd_eligible(p.id) ORDER BY p.id LIMIT 1;
 SELECT jsonb_agg(jsonb_build_object('PARNO',lpad(n::text,18,'0'),'SALE1_AMT',10000,'SALE1_DATE',current_date-30)) INTO sample FROM generate_series(1,3)n;
 UPDATE public.due_diligence_reviews SET zoning_status='verified_gis',access_status='mapped_road_proximity_verified',
 findings=findings||jsonb_build_object(
 'zoning',jsonb_build_object('checked_at',stamp,'attributes',jsonb_build_object('classification','residential')),
 'flood',jsonb_build_object('checked_at',stamp,'source_error',false,'zone','X'),
 'wetlands',jsonb_build_object('checked_at',stamp,'source_error',false,'hit',false),
 'access',jsonb_build_object('checked_at',stamp,'nearby_road',jsonb_build_object('name','fixture')),
 'comps',jsonb_build_object('checked_at',stamp,'source_error',false,'sample',sample))
 WHERE property_id=target AND status='review_required';
 IF NOT public.god_mode_seller_gate(target,'enrichment') THEN RAISE EXCEPTION 'Initial screen did not permit enrichment'; END IF;
 IF god_mode_ops.seller_deep_dd_eligible(target) THEN RAISE EXCEPTION 'Initial screen incorrectly approved deep DD'; END IF;
 BEGIN
  PERFORM god_mode_ops.require_transaction_release();
  RAISE EXCEPTION 'Transaction release incorrectly opened';
 EXCEPTION WHEN SQLSTATE '55000' THEN
  IF SQLERRM<>'TRANSACTION_RELEASE_REQUIRED' THEN RAISE; END IF;
 END;
END $test$;
ROLLBACK;
