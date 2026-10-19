-- Read-only bridge from PR #17 qualification evidence to Judy review.
-- No automatic seller-card release; the source does not certify all gates.
CREATE OR REPLACE VIEW god_mode_ops.v_judy_qualification_mapping WITH (security_invoker=true) AS
SELECT q.parcel_id AS strap, s.county, s.owner_name, s.site_address AS property_address,
       q.status AS qualification_status,
       coalesce(q.checks->>'reason', array_to_string(ARRAY(SELECT jsonb_array_elements_text(coalesce(q.checks->'missing','[]'::jsonb))), ', '), 'UNVERIFIED') AS buildability_status,
       CASE WHEN coalesce((q.checks->>'eligible_for_contact')::boolean,false) IS TRUE
                 AND q.status='QUALIFIED' AND s.seller_distress_status='VERIFIED'
            THEN 'DISTRESS_REVIEW_REQUIRED' ELSE 'UNVERIFIED_DISTRESS' END AS financial_distress_reason,
       CASE WHEN coalesce((q.checks->>'eligible_for_contact')::boolean,false) IS TRUE
                 AND q.status='QUALIFIED' AND s.seller_distress_status='VERIFIED'
            THEN 'REVIEW_PROPERTY_WITH_OWNER' ELSE NULL END AS conversation_hook,
       false AS seller_card_release_authorized,
       q.evaluated_at
FROM god_mode_ops.parcel_qualification_results q
JOIN god_mode_ops.buyer_demand_land_screening s
  ON s.parcel_id=q.parcel_id AND lower(s.county) IN ('lee','charlotte');
REVOKE ALL ON god_mode_ops.v_judy_qualification_mapping FROM PUBLIC, anon, authenticated;
