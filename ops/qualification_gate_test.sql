do $$
begin
  assert god_mode_ops.evaluate_gis_gate(
    '{"flood":{"result":"FOUND","features":[{"attributes":{"FLD_ZONE":"AE","SFHA_TF":"T"}}]},"wetlands":{"result":"NOT_FOUND"},"road":{"result":"FOUND","paved_candidate_count":2},"legal":false,"distress":false}') ->> 'status' = 'REJECTED_FLOOD_ZONE';
  assert god_mode_ops.evaluate_gis_gate(
    '{"flood":{"result":"ERROR"},"wetlands":{"result":"NOT_FOUND"},"road":{"result":"FOUND","paved_candidate_count":2},"legal":true,"distress":true}') ->> 'status' = 'HOLD';
  assert god_mode_ops.evaluate_gis_gate(
    '{"flood":{"result":"FOUND","features":[{"attributes":{"FLD_ZONE":"X","SFHA_TF":"F","ZONE_SUBTY":"AREA OF MINIMAL FLOOD HAZARD"}}]},"wetlands":{"result":"NOT_FOUND"},"road":{"result":"FOUND","paved_candidate_count":2},"legal":false,"distress":true}') ->> 'status' = 'HOLD';
  assert god_mode_ops.evaluate_gis_gate('{}')->>'status'='HOLD';
  assert god_mode_ops.evaluate_gis_gate(
    '{"flood":{"result":"FOUND","features":[{"attributes":{"FLD_ZONE":"X","SFHA_TF":"F","ZONE_SUBTY":"AREA OF MINIMAL FLOOD HAZARD"}}]},"wetlands":{"result":"NOT_FOUND","feature_count":0},"road":{"result":"FOUND","paved_candidate_count":2},"legal":true,"distress":true,"initial_checks":true}') ->> 'status' = 'QUALIFIED_FOR_CONTACT_ENRICHMENT';
  assert god_mode_ops.evaluate_gis_gate(
    '{"flood":{"result":"FOUND","features":[{"attributes":{"FLD_ZONE":"X","SFHA_TF":"F","ZONE_SUBTY":"AREA OF MINIMAL FLOOD HAZARD"}}]},"wetlands":{"result":"NOT_FOUND","feature_count":0},"road":{"result":"FOUND","paved_candidate_count":"bad"},"legal":true,"distress":true,"initial_checks":true}') ->> 'status' = 'HOLD';
  assert god_mode_ops.evaluate_gis_gate(
    '{"flood":{"result":"FOUND","features":[{"attributes":{"FLD_ZONE":"X","SFHA_TF":"F","ZONE_SUBTY":"AREA OF MINIMAL FLOOD HAZARD"}}]},"wetlands":{"result":"NOT_FOUND","feature_count":0},"road":{"result":"FOUND","paved_candidate_count":[]},"legal":true,"distress":true,"initial_checks":true}') ->> 'status' = 'HOLD';
end $$;
