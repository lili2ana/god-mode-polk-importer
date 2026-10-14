-- Backend intake for authorized download/OCR results. Never updates VERIFIED fields.
create or replace function god_mode_ops.stage_recorded_document(d jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
begin
 if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
 if d->>'status' is distinct from 'REVIEW_REQUIRED'
  or d->>'document_url' not like 'https://%' or d->>'document_url' is null
  or d->>'document_sha256' !~ '^[a-f0-9]{64}$' or d->>'document_sha256' is null
  or nullif(trim(d->>'instrument_reference'),'') is null
  or jsonb_typeof(d->'evidence_candidates') is distinct from 'array'
  or d->'exact_frontage_verified' is distinct from 'false'::jsonb
  or d->'legal_access_verified' is distinct from 'false'::jsonb then raise exception 'invalid unapproved document evidence'; end if;
 insert into god_mode_ops.parcel_access_reviews(parcel_id,research)
 values(d->>'parcel_id',jsonb_build_object('recorded_document',d,'document_staged_at',now()))
 on conflict(parcel_id) do update set research=parcel_access_reviews.research||excluded.research;
 return '{"staged":true,"legal_access_auto_approved":false}';
end $$;
revoke all on function god_mode_ops.stage_recorded_document(jsonb) from public,anon,authenticated;
grant execute on function god_mode_ops.stage_recorded_document(jsonb) to service_role;
-- Pin the specific missing record, not an inferred frontage number.
update god_mode_ops.parcel_access_reviews
set research=research||'{"record_request":{"county":"Lee","plat_book":15,"plat_page":100,"unit":3,"block":16,"lot":2,"status":"AUTHORIZED_DOCUMENT_REQUIRED","source":"https://www.leeclerk.org/departments/official-records-services/search-official-records"}}'
where parcel_id='334426L3030160020';
