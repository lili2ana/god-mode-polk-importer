-- Canonical backend schema. Provider history is a candidate, never current proof.
create table if not exists god_mode_ops.distress_evidence (
 parcel_id text not null references god_mode_ops.buyer_demand_land_screening(parcel_id),
 source_name text not null, checked_at timestamptz not null default now(), evidence jsonb not null,
 primary key(parcel_id,source_name)
);
alter table god_mode_ops.distress_evidence enable row level security;
revoke all on god_mode_ops.distress_evidence from public,anon,authenticated;
grant select,insert,update on god_mode_ops.distress_evidence to service_role;

create or replace function god_mode_ops.distress_evidence_qualifies(d jsonb,p_parcel text,p_county text,p_owner text)
returns boolean language plpgsql stable set search_path='' as $$
begin
 if d->>'parcel_id' is distinct from p_parcel or lower(d->>'county') is distinct from lower(p_county)
  or nullif(trim(p_owner),'') is null or d->>'current_owner' is distinct from p_owner
  or d->>'verification_status' is distinct from 'VERIFIED'
  or d->'active' is distinct from 'true'::jsonb or d->'released' is distinct from 'false'::jsonb
  or d->'release_search_complete' is distinct from 'true'::jsonb
  or not coalesce((d->>'checked_at')::timestamptz between now()-interval '7 days' and now(),false)
  or not coalesce((d->>'release_checked_at')::timestamptz between now()-interval '7 days' and now(),false)
  or not coalesce((d->>'event_date')::date<=current_date,false)
  or nullif(trim(d->>'source_record_id'),'') is null
  or not coalesce(d->>'source_url' like 'https://%',false)
  or not coalesce(d->>'record_sha256' ~ '^[a-f0-9]{64}$',false) then return false; end if;
 return case d->>'signal_type'
  when 'lis_pendens' then true
  when 'notice_of_default' then true
  when 'tax_certificate_delinquency' then coalesce((d->>'amount_due')::numeric>0
   and (d->>'delinquent_since')::date < (current_date-interval '12 months')::date,false)
  else false end;
exception when others then return false;
end $$;

create or replace function god_mode_ops.stage_distress_candidate(d jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
begin
 if current_user not in ('postgres','service_role') then raise exception 'backend only'; end if;
 if d->>'source_name' not in ('ATTOM','official_clerk','official_tax_collector')
  or d->>'source_name' is null or d->>'record_sha256' !~ '^[a-f0-9]{64}$'
  or d->>'record_sha256' is null or d->>'source_url' not like 'https://%' or d->>'source_url' is null
  then raise exception 'invalid source evidence'; end if;
 -- Even a caller-submitted VERIFIED label does not certify the record.
 d:=d||jsonb_build_object('verification_status','REVIEW_REQUIRED','qualifying_distress',false,'checked_at',now());
 insert into god_mode_ops.distress_evidence(parcel_id,source_name,evidence)
 values(d->>'parcel_id',d->>'source_name',d)
 on conflict(parcel_id,source_name) do update set evidence=excluded.evidence,checked_at=excluded.checked_at;
 return '{"staged":true,"distress_auto_approved":false}';
end $$;

create or replace function god_mode_ops.county_distress_verified(p_parcel text,p_county text)
returns boolean language sql stable set search_path='' as $$
select coalesce((select case when lower(p_county)='polk' then god_mode_ops.seller_dd_eligible(p.id)
 else exists(select 1 from god_mode_ops.distress_evidence e where e.parcel_id=p.parcel_id
  and god_mode_ops.distress_evidence_qualifies(e.evidence,p.parcel_id,p.county,p.owner_name)) end
 from public.properties p where p.parcel_id=p_parcel and lower(p.county)=lower(p_county)),false)
$$;
revoke all on function god_mode_ops.distress_evidence_qualifies(jsonb,text,text,text),god_mode_ops.stage_distress_candidate(jsonb),god_mode_ops.county_distress_verified(text,text) from public,anon,authenticated;
grant execute on function god_mode_ops.distress_evidence_qualifies(jsonb,text,text,text),god_mode_ops.stage_distress_candidate(jsonb),god_mode_ops.county_distress_verified(text,text) to service_role;
