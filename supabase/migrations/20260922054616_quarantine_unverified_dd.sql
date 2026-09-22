-- Keep a private, complete rollback copy before neutralizing legacy machine
-- estimates. Human-approved and already-safe reviews are not changed.
CREATE TABLE god_mode_ops.dd_security_backup (
  review_id uuid PRIMARY KEY,original_row jsonb NOT NULL,
  captured_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.dd_security_backup ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.dd_security_backup FROM PUBLIC,anon,authenticated,service_role;
DO $quarantine$
DECLARE affected bigint;
BEGIN
 LOCK TABLE public.due_diligence_reviews IN SHARE ROW EXCLUSIVE MODE;
 SELECT count(*) INTO affected FROM public.due_diligence_reviews
 WHERE status='review_required' AND findings->'underwriting'->>'eligible_for_automated_acquisition' IS DISTINCT FROM 'false';
 IF affected>100 THEN RAISE EXCEPTION 'Unexpected review population; inspect before quarantine'; END IF;
 INSERT INTO god_mode_ops.dd_security_backup(review_id,original_row)
 SELECT d.id,to_jsonb(d) FROM public.due_diligence_reviews d
 WHERE status='review_required' AND findings->'underwriting'->>'eligible_for_automated_acquisition' IS DISTINCT FROM 'false';
 UPDATE public.due_diligence_reviews d SET dd_score=0,comps_status='review_required',underwriting_status='review_required',
 findings=jsonb_set(coalesce(d.findings,'{}'::jsonb),'{underwriting}',jsonb_build_object(
   'checked_at',now(),'estimated_value',NULL,'preliminary_mao',NULL,'confidence_score',NULL,
   'eligible_for_automated_acquisition',false,'note','Legacy machine estimates quarantined pending verified comps and due diligence; original review retained privately.'))
 WHERE d.id IN(SELECT review_id FROM god_mode_ops.dd_security_backup);
END $quarantine$;
