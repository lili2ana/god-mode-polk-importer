do $$ declare d jsonb;
begin
 d:=god_mode_ops.osceola_road_network_check('052527409200011890');
 if d->'parcel_verified' is distinct from 'true'::jsonb or d->'road_query_ok' is distinct from 'true'::jsonb then raise exception 'official network check failed'; end if;
 if d->>'pavement_status' is distinct from 'REVIEW_REQUIRED' or d->'legal_access_verified' is distinct from 'false'::jsonb then raise exception 'unverified pavement/legal access approved'; end if;
end $$;
