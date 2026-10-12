-- Consume verified county-specific zoning/comparable evidence; no Polk ID assumptions.
create or replace function god_mode_ops.county_preflight_pass(c jsonb,p_county text,p_subject text,p_acres numeric)
returns boolean language plpgsql stable set search_path = '' as $$
declare n integer;
begin
  if lower(c->>'county') is distinct from lower(p_county)
    or c->'zoning_verified' is distinct from 'true'::jsonb
    or c->'source_verified' is distinct from 'true'::jsonb
    or not god_mode_ops.screen_check_fresh(c) or not coalesce(p_acres>0,false)
    or jsonb_typeof(c->'sample') is distinct from 'array' then return false; end if;
  select count(distinct v->>'parcel_id') into n from jsonb_array_elements(c->'sample') v
  where v->>'parcel_id'<>p_subject and length(v->>'parcel_id')>0
    and lower(v->>'county')=lower(p_county) and v->>'asset_type'='land'
    and v->'verified'='true'::jsonb
    and jsonb_typeof(v->'price')='number' and (v->>'price')::numeric>0
    and jsonb_typeof(v->'acreage')='number' and (v->>'acreage')::numeric between p_acres*0.5 and p_acres*2
    and pg_input_is_valid(v->>'sale_date','date')
    and (v->>'sale_date')::date between (current_date-interval '2 years')::date and current_date;
  return n>=3;
exception when others then return false;
end $$;

create or replace function god_mode_ops.county_distress_verified(p_parcel text,p_county text)
returns boolean language sql stable set search_path = '' as $$
select coalesce((select case when lower(p_county)='polk' then god_mode_ops.seller_dd_eligible(p.id)
else exists (
  select 1 from public.property_distress_signals s
  where s.property_id=p.id and s.parcel_id=p.parcel_id and s.signal_status='verified'
    and s.verified_at between now()-interval '7 days' and now()
    and length(s.source_record_id)>0 and s.source_url like 'https://%'
    and s.raw_data->>'current_pa_parcel_id'=p.parcel_id
    and s.raw_data->>'current_pa_owner'=p.owner_name
    and s.raw_data->>'source_document_sha256' ~ '^[a-f0-9]{64}$'
    and ((s.signal_type='lis_pendens_recorded' and s.raw_data->'subsequent_release_found'='false'::jsonb)
      or (s.signal_type='tax_delinquent' and s.amount>0)
      or (s.signal_type='absentee_longheld_vacant' and s.raw_data->'absentee'='true'::jsonb
        and s.raw_data->'vacant'='true'::jsonb and jsonb_typeof(s.raw_data->'held_years')='number'
        and (s.raw_data->>'held_years')::numeric>=10))
) end from public.properties p where p.parcel_id=p_parcel and lower(p.county)=lower(p_county)),false)
$$;
revoke all on function god_mode_ops.county_preflight_pass(jsonb,text,text,numeric),god_mode_ops.county_distress_verified(text,text) from public,anon,authenticated;
grant execute on function god_mode_ops.county_preflight_pass(jsonb,text,text,numeric),god_mode_ops.county_distress_verified(text,text) to service_role;
