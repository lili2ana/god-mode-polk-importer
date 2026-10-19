begin;
do $$ declare rejected boolean:=false; d jsonb; result jsonb;
begin
 d:=jsonb_build_object('parcel_id','334426L3030160020','document_url','https://example.org/test.pdf',
 'document_sha256',repeat('a',64),'instrument_reference','TEST ONLY','evidence_candidates','[]'::jsonb,
 'status','REVIEW_REQUIRED','exact_frontage_verified',false,'legal_access_verified',false);
 result:=god_mode_ops.stage_recorded_document(d);
 if result->'legal_access_auto_approved'<>'false'::jsonb then raise exception 'intake approved access'; end if;
 begin perform god_mode_ops.stage_recorded_document(d||'{"legal_access_verified":true}');
 exception when others then rejected:=true; end;
 if not rejected then raise exception 'approval payload accepted'; end if;
end $$;
rollback;
