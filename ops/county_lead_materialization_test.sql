-- All synthetic records roll back; no contact information or messages.
begin;
insert into god_mode_ops.buyer_demand_land_screening(parcel_id,county,city,site_address,source_url,gis_acres,building_count,
  screening_status,flood_status,wetlands_status,paved_road_status,seller_distress_status)
values('COUNTY_AUTOMATION_TEST','Lee','Lehigh Acres','SYNTHETIC TEST ONLY','https://example.org/synthetic-test',0.25,0,'QUALIFIED_FOR_CONTACT_ENRICHMENT','PASS','PASS','PASS','VERIFIED');
insert into public.properties(parcel_id,county,city,state,property_type)
values('COUNTY_AUTOMATION_TEST','Lee','Lehigh Acres','FL','land');
insert into god_mode_ops.parcel_qualification_results(parcel_id,status,checks)
values('COUNTY_AUTOMATION_TEST','QUALIFIED_FOR_CONTACT_ENRICHMENT','{"eligible_for_contact":true}');
insert into god_mode_ops.parcel_access_reviews(parcel_id,status,document_url,document_sha256,
 instrument_reference,recorded_frontage_ft,parcel_identity_verified,legal_access_verified,paved_frontage_verified,
 current_restrictions_checked,reviewed_by,reviewed_at,expires_at)
values('COUNTY_AUTOMATION_TEST','VERIFIED','https://example.org/synthetic-test',repeat('0',64),
 'SYNTHETIC TEST ONLY',80,true,true,true,true,'00000000-0000-0000-0000-000000000001',now(),now()+interval '1 day');
select god_mode_ops.materialize_county_qualified_leads();
do $$ begin
 assert (select count(*)=1 from public.leads l join public.properties p on p.id=l.property_id where p.parcel_id='COUNTY_AUTOMATION_TEST');
 assert (select phone is null and email is null and outreach_status='held' from public.leads l join public.properties p on p.id=l.property_id where p.parcel_id='COUNTY_AUTOMATION_TEST');
end $$;
select god_mode_ops.materialize_county_qualified_leads();
do $$ begin
 assert (select count(*)=1 from public.leads l join public.properties p on p.id=l.property_id where p.parcel_id='COUNTY_AUTOMATION_TEST');
end $$;
rollback;
