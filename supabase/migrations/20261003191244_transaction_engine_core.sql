
create schema if not exists god_mode_ops;

alter table public.deals
  add column if not exists earnest_money numeric not null default 0,
  add column if not exists target_assignment_fee numeric not null default 0,
  add column if not exists exit_strategy text not null default 'wholesale';

create table if not exists god_mode_ops.deal_gate_catalog (
  gate_name text primary key,
  required_for_state text not null,
  allow_human_waiver boolean not null default false,
  description text not null
);

insert into god_mode_ops.deal_gate_catalog(gate_name, required_for_state, allow_human_waiver, description)
values
  ('CONTACT_IDENTITY','OFFER_READY',false,'Verified seller/owner contact identity'),
  ('COMPLIANCE','OFFER_READY',false,'DNC/consent/compliance clearance before seller outreach or offer workflow'),
  ('TITLE_LIENS','OFFER_READY',false,'Title/liens review cleared'),
  ('ACCESS','OFFER_READY',false,'Access review cleared'),
  ('SOURCE_RISK','OFFER_READY',true,'Source-risk review cleared or human-waived'),
  ('COMPS_QUALIFIED','OFFER_READY',false,'Recorded-sale comps qualified, not merely screened'),
  ('UNDERWRITING','OFFER_READY',false,'Underwriting cleared'),
  ('LEGAL_DESCRIPTION','OFFER_READY',false,'Legal description available for contract drafting'),
  ('ECONOMICS','OFFER_READY',false,'ARV/MAO/offer/EMD/assignment-fee inputs complete and internally consistent')
on conflict (gate_name) do update
set required_for_state = excluded.required_for_state,
    allow_human_waiver = excluded.allow_human_waiver,
    description = excluded.description;

create table if not exists god_mode_ops.deal_workflow (
  deal_id uuid primary key references public.deals(id) on delete cascade,
  state text not null default 'QUALIFIED'
    check (state in ('QUALIFIED','OFFER_READY','HUMAN_APPROVED','CONTRACT_DRAFTED','SIGNATURE_READY','EXECUTED','DISPOSITION_READY','CLOSED','HOLD','REVIEW')),
  state_version integer not null default 1 check (state_version > 0),
  blocker_code text,
  blocker_detail text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists god_mode_ops.deal_gate_status (
  deal_id uuid not null references public.deals(id) on delete cascade,
  gate_name text not null references god_mode_ops.deal_gate_catalog(gate_name),
  status text not null check (status in ('PASS','BLOCKED','REVIEW','WAIVED')),
  evidence jsonb not null default '{}'::jsonb,
  evidence_hash text,
  checked_by text not null default 'system',
  checked_at timestamptz not null default now(),
  waiver_actor text,
  waiver_reason text,
  primary key (deal_id, gate_name),
  check (
    status <> 'WAIVED'
    or (waiver_actor is not null and btrim(waiver_actor) <> '' and waiver_reason is not null and btrim(waiver_reason) <> '')
  )
);

create table if not exists god_mode_ops.deal_evidence_snapshots (
  id uuid primary key default gen_random_uuid(),
  deal_id uuid not null references public.deals(id) on delete cascade,
  snapshot_version integer not null,
  snapshot_hash text not null,
  snapshot jsonb not null,
  created_at timestamptz not null default now(),
  unique (deal_id, snapshot_version),
  unique (deal_id, snapshot_hash)
);

create table if not exists god_mode_ops.contract_templates (
  id uuid primary key default gen_random_uuid(),
  template_key text not null,
  version integer not null check (version > 0),
  jurisdiction text not null,
  asset_type text not null,
  contract_type text not null check (contract_type in ('ACQUISITION','ASSIGNMENT')),
  content text not null,
  required_variables text[] not null default '{}'::text[],
  status text not null default 'DRAFT' check (status in ('DRAFT','APPROVED','RETIRED')),
  approved_by text,
  approved_at timestamptz,
  effective_from date,
  effective_to date,
  created_at timestamptz not null default now(),
  unique (template_key, version),
  check (
    status <> 'APPROVED'
    or (approved_by is not null and btrim(approved_by) <> '' and approved_at is not null)
  )
);

create table if not exists god_mode_ops.contracts (
  id uuid primary key default gen_random_uuid(),
  deal_id uuid not null references public.deals(id) on delete cascade,
  contract_type text not null check (contract_type in ('ACQUISITION','ASSIGNMENT')),
  template_id uuid not null references god_mode_ops.contract_templates(id),
  deal_snapshot_id uuid not null references god_mode_ops.deal_evidence_snapshots(id),
  status text not null default 'DRAFTED'
    check (status in ('DRAFTED','SIGNATURE_READY','EXECUTED','VOID')),
  binding_status text not null default 'NON_BINDING'
    check (binding_status in ('NON_BINDING','BINDING')),
  rendered_payload jsonb not null,
  document_hash text not null,
  idempotency_key text not null unique,
  supersedes_contract_id uuid references god_mode_ops.contracts(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  executed_at timestamptz
);

create table if not exists god_mode_ops.deal_approvals (
  id uuid primary key default gen_random_uuid(),
  deal_id uuid not null references public.deals(id) on delete cascade,
  deal_snapshot_id uuid not null references god_mode_ops.deal_evidence_snapshots(id),
  approval_type text not null
    check (approval_type in ('OFFER','ACQUISITION_SIGNATURE','ASSIGNMENT_SIGNATURE','SOURCE_RISK_WAIVER')),
  actor_type text not null check (actor_type = 'human_operator'),
  actor_id text not null,
  approved_at timestamptz not null default now(),
  revoked_at timestamptz,
  notes text
);

create unique index if not exists deal_approvals_active_unique
on god_mode_ops.deal_approvals(deal_id, deal_snapshot_id, approval_type)
where revoked_at is null;

create table if not exists god_mode_ops.esign_envelopes (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null references god_mode_ops.contracts(id) on delete cascade,
  provider text not null,
  idempotency_key text not null unique,
  external_envelope_id text,
  status text not null default 'PREPARED'
    check (status in ('PREPARED','SENT','COMPLETED','VOID','FAILED')),
  request_payload jsonb not null default '{}'::jsonb,
  response_payload jsonb not null default '{}'::jsonb,
  sent_at timestamptz,
  completed_at timestamptz,
  executed_document_ref text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    status <> 'COMPLETED'
    or (completed_at is not null and executed_document_ref is not null and btrim(executed_document_ref) <> '')
  )
);

create table if not exists god_mode_ops.disposition_packages (
  id uuid primary key default gen_random_uuid(),
  deal_id uuid not null references public.deals(id) on delete cascade,
  acquisition_contract_id uuid not null references god_mode_ops.contracts(id),
  buyer_match_id uuid references public.buyer_matches(id),
  buyer_id uuid references public.buyers(id),
  status text not null default 'READY'
    check (status in ('READY','CONTACT_READY','ASSIGNMENT_DRAFTED','SIGNATURE_READY','ASSIGNED','CLOSED','HOLD')),
  package jsonb not null default '{}'::jsonb,
  package_hash text not null,
  idempotency_key text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (deal_id, buyer_id)
);

create table if not exists god_mode_ops.buyer_message_outbox (
  id uuid primary key default gen_random_uuid(),
  deal_id uuid not null references public.deals(id) on delete cascade,
  disposition_package_id uuid not null references god_mode_ops.disposition_packages(id) on delete cascade,
  buyer_id uuid not null references public.buyers(id),
  channel text not null check (channel in ('EMAIL','SMS','WHATSAPP')),
  recipient text not null,
  status text not null default 'HELD' check (status in ('HELD','READY','SENT','FAILED','CANCELLED')),
  blocker_code text,
  payload jsonb not null default '{}'::jsonb,
  idempotency_key text not null unique,
  external_message_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists god_mode_ops.deal_state_history (
  id bigint generated by default as identity primary key,
  deal_id uuid not null references public.deals(id) on delete cascade,
  from_state text,
  to_state text not null,
  actor_type text not null,
  actor_id text,
  reason text,
  created_at timestamptz not null default now()
);

alter table god_mode_ops.deal_gate_catalog enable row level security;
alter table god_mode_ops.deal_workflow enable row level security;
alter table god_mode_ops.deal_gate_status enable row level security;
alter table god_mode_ops.deal_evidence_snapshots enable row level security;
alter table god_mode_ops.contract_templates enable row level security;
alter table god_mode_ops.contracts enable row level security;
alter table god_mode_ops.deal_approvals enable row level security;
alter table god_mode_ops.esign_envelopes enable row level security;
alter table god_mode_ops.disposition_packages enable row level security;
alter table god_mode_ops.buyer_message_outbox enable row level security;
alter table god_mode_ops.deal_state_history enable row level security;

revoke all on god_mode_ops.deal_gate_catalog,
              god_mode_ops.deal_workflow,
              god_mode_ops.deal_gate_status,
              god_mode_ops.deal_evidence_snapshots,
              god_mode_ops.contract_templates,
              god_mode_ops.contracts,
              god_mode_ops.deal_approvals,
              god_mode_ops.esign_envelopes,
              god_mode_ops.disposition_packages,
              god_mode_ops.buyer_message_outbox,
              god_mode_ops.deal_state_history
from public, anon, authenticated;

grant usage on schema god_mode_ops to service_role;
grant select, insert, update, delete on god_mode_ops.deal_gate_catalog,
              god_mode_ops.deal_workflow,
              god_mode_ops.deal_gate_status,
              god_mode_ops.deal_evidence_snapshots,
              god_mode_ops.contract_templates,
              god_mode_ops.contracts,
              god_mode_ops.deal_approvals,
              god_mode_ops.esign_envelopes,
              god_mode_ops.disposition_packages,
              god_mode_ops.buyer_message_outbox,
              god_mode_ops.deal_state_history
to service_role;
grant usage, select on all sequences in schema god_mode_ops to service_role;

create or replace function god_mode_ops.calculate_deal_economics(
  p_arv numeric,
  p_repairs numeric,
  p_closing numeric,
  p_holding numeric,
  p_risk_reserve numeric,
  p_target_assignment_fee numeric,
  p_earnest_money numeric
)
returns table (
  mao numeric,
  target_offer numeric,
  expected_gross_profit numeric,
  earnest_money numeric,
  target_assignment_fee numeric
)
language sql
immutable
set search_path = pg_catalog
as $$
  select
    greatest(0, coalesce(p_arv,0)
      - greatest(0,coalesce(p_repairs,0))
      - greatest(0,coalesce(p_closing,0))
      - greatest(0,coalesce(p_holding,0))
      - greatest(0,coalesce(p_risk_reserve,0))
      - greatest(0,coalesce(p_target_assignment_fee,0))) as mao,
    greatest(0, coalesce(p_arv,0)
      - greatest(0,coalesce(p_repairs,0))
      - greatest(0,coalesce(p_closing,0))
      - greatest(0,coalesce(p_holding,0))
      - greatest(0,coalesce(p_risk_reserve,0))
      - greatest(0,coalesce(p_target_assignment_fee,0))) as target_offer,
    greatest(0,coalesce(p_target_assignment_fee,0)) as expected_gross_profit,
    greatest(0,coalesce(p_earnest_money,0)) as earnest_money,
    greatest(0,coalesce(p_target_assignment_fee,0)) as target_assignment_fee;
$$;

create or replace function god_mode_ops.set_deal_economics(
  p_deal_id uuid,
  p_arv numeric,
  p_repairs numeric,
  p_closing numeric,
  p_holding numeric,
  p_risk_reserve numeric,
  p_target_assignment_fee numeric,
  p_earnest_money numeric
)
returns public.deals
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  v_calc record;
  v_deal public.deals%rowtype;
begin
  if p_arv is null or p_arv <= 0 then
    raise exception 'ARV must be positive';
  end if;
  if least(
      coalesce(p_repairs,0),
      coalesce(p_closing,0),
      coalesce(p_holding,0),
      coalesce(p_risk_reserve,0),
      coalesce(p_target_assignment_fee,0),
      coalesce(p_earnest_money,0)
    ) < 0 then
    raise exception 'Deal economics inputs cannot be negative';
  end if;

  select * into v_calc
  from god_mode_ops.calculate_deal_economics(
    p_arv,p_repairs,p_closing,p_holding,p_risk_reserve,p_target_assignment_fee,p_earnest_money
  );

  update public.deals
  set estimated_value = p_arv,
      repair_cost = coalesce(p_repairs,0),
      closing_cost = coalesce(p_closing,0),
      holding_cost = coalesce(p_holding,0),
      risk_reserve = coalesce(p_risk_reserve,0),
      target_profit = coalesce(p_target_assignment_fee,0),
      target_assignment_fee = v_calc.target_assignment_fee,
      earnest_money = v_calc.earnest_money,
      mao = v_calc.mao,
      target_offer = v_calc.target_offer,
      expected_exit_price = p_arv,
      expected_gross_profit = v_calc.expected_gross_profit,
      expected_net_profit = v_calc.expected_gross_profit,
      updated_at = now()
  where id = p_deal_id
  returning * into v_deal;

  if not found then
    raise exception 'Deal % not found', p_deal_id;
  end if;

  return v_deal;
end;
$$;

create or replace function god_mode_ops.create_deal_snapshot(p_deal_id uuid)
returns uuid
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  v_payload jsonb;
  v_hash text;
  v_version integer;
  v_id uuid;
begin
  select jsonb_build_object(
    'deal', jsonb_build_object(
      'id', d.id,
      'property_id', d.property_id,
      'lead_id', d.lead_id,
      'stage', d.stage,
      'estimated_value', d.estimated_value,
      'repair_cost', d.repair_cost,
      'closing_cost', d.closing_cost,
      'holding_cost', d.holding_cost,
      'risk_reserve', d.risk_reserve,
      'target_assignment_fee', d.target_assignment_fee,
      'earnest_money', d.earnest_money,
      'mao', d.mao,
      'target_offer', d.target_offer,
      'expected_exit_price', d.expected_exit_price,
      'exit_strategy', d.exit_strategy
    ),
    'property', jsonb_build_object(
      'parcel_id', p.parcel_id,
      'address', p.address,
      'city', p.city,
      'state', p.state,
      'zip', p.zip,
      'county', p.county,
      'property_type', p.property_type,
      'acreage', p.acreage,
      'owner_name', p.owner_name
    ),
    'lead', jsonb_build_object(
      'contact_name', l.contact_name,
      'phone_present', (l.phone is not null and btrim(l.phone) <> ''),
      'email_present', (l.email is not null and btrim(l.email) <> '')
    ),
    'latest_due_diligence', (
      select jsonb_build_object(
        'review_id', dd.id,
        'status', dd.status,
        'title_status', dd.title_status,
        'access_status', dd.access_status,
        'comps_status', dd.comps_status,
        'underwriting_status', dd.underwriting_status,
        'fatal_flags', dd.fatal_flags,
        'completed_at', dd.completed_at
      )
      from public.due_diligence_reviews dd
      where dd.property_id = d.property_id
      order by dd.updated_at desc
      limit 1
    ),
    'legal_description', (
      select string_agg(nullif(btrim(pl.dscr),''), ' ' order by pl.num)
      from public.polk_legal_v2 pl
      where pl.parcel_id = p.parcel_id
    ),
    'gates', (
      select coalesce(jsonb_object_agg(gs.gate_name, jsonb_build_object(
        'status', gs.status,
        'evidence_hash', gs.evidence_hash,
        'checked_at', gs.checked_at,
        'waiver_actor', gs.waiver_actor,
        'waiver_reason', gs.waiver_reason
      )), '{}'::jsonb)
      from god_mode_ops.deal_gate_status gs
      where gs.deal_id = d.id
    )
  )
  into v_payload
  from public.deals d
  join public.properties p on p.id = d.property_id
  left join public.leads l on l.id = d.lead_id
  where d.id = p_deal_id;

  if v_payload is null then
    raise exception 'Deal % not found or has no property', p_deal_id;
  end if;

  v_hash := encode(digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');

  select id into v_id
  from god_mode_ops.deal_evidence_snapshots
  where deal_id = p_deal_id and snapshot_hash = v_hash;

  if v_id is not null then
    return v_id;
  end if;

  select coalesce(max(snapshot_version),0) + 1
  into v_version
  from god_mode_ops.deal_evidence_snapshots
  where deal_id = p_deal_id;

  insert into god_mode_ops.deal_evidence_snapshots(
    deal_id, snapshot_version, snapshot_hash, snapshot
  )
  values (p_deal_id, v_version, v_hash, v_payload)
  returning id into v_id;

  return v_id;
end;
$$;

create or replace function god_mode_ops.set_gate(
  p_deal_id uuid,
  p_gate_name text,
  p_status text,
  p_evidence jsonb default '{}'::jsonb,
  p_checked_by text default 'system',
  p_waiver_actor text default null,
  p_waiver_reason text default null
)
returns void
language plpgsql
security invoker
set search_path = pg_catalog, god_mode_ops
as $$
declare
  v_allow_waiver boolean;
  v_hash text;
begin
  select allow_human_waiver into v_allow_waiver
  from god_mode_ops.deal_gate_catalog
  where gate_name = p_gate_name;

  if v_allow_waiver is null then
    raise exception 'Unknown gate %', p_gate_name;
  end if;

  if p_status not in ('PASS','BLOCKED','REVIEW','WAIVED') then
    raise exception 'Invalid gate status %', p_status;
  end if;

  if p_status = 'WAIVED' and not v_allow_waiver then
    raise exception 'Gate % cannot be waived', p_gate_name;
  end if;

  if p_status = 'WAIVED' and (p_waiver_actor is null or p_waiver_reason is null) then
    raise exception 'Waiver requires human actor and reason';
  end if;

  v_hash := encode(digest(convert_to(coalesce(p_evidence,'{}'::jsonb)::text,'UTF8'),'sha256'),'hex');

  insert into god_mode_ops.deal_gate_status(
    deal_id, gate_name, status, evidence, evidence_hash, checked_by, checked_at, waiver_actor, waiver_reason
  )
  values (
    p_deal_id, p_gate_name, p_status, coalesce(p_evidence,'{}'::jsonb), v_hash,
    coalesce(nullif(btrim(p_checked_by),''),'system'), now(), p_waiver_actor, p_waiver_reason
  )
  on conflict (deal_id, gate_name) do update
  set status = excluded.status,
      evidence = excluded.evidence,
      evidence_hash = excluded.evidence_hash,
      checked_by = excluded.checked_by,
      checked_at = excluded.checked_at,
      waiver_actor = excluded.waiver_actor,
      waiver_reason = excluded.waiver_reason;
end;
$$;

create or replace function god_mode_ops.refresh_deal_gates(p_deal_id uuid)
returns void
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  v_deal public.deals%rowtype;
  v_property public.properties%rowtype;
  v_dd public.due_diligence_reviews%rowtype;
  v_has_legal boolean;
begin
  select * into v_deal from public.deals where id = p_deal_id;
  if not found then
    raise exception 'Deal % not found', p_deal_id;
  end if;

  select * into v_property from public.properties where id = v_deal.property_id;

  select * into v_dd
  from public.due_diligence_reviews
  where property_id = v_deal.property_id
  order by updated_at desc
  limit 1;

  perform god_mode_ops.set_gate(
    p_deal_id,
    'TITLE_LIENS',
    case when v_dd.id is not null and v_dd.status = 'completed'
              and v_dd.title_status in ('cleared','verified','passed','title_clear')
         then 'PASS' else 'BLOCKED' end,
    jsonb_build_object('review_id',v_dd.id,'status',v_dd.status,'title_status',v_dd.title_status),
    'dd-refresh'
  );

  perform god_mode_ops.set_gate(
    p_deal_id,
    'ACCESS',
    case when v_dd.id is not null
              and v_dd.access_status in ('cleared','verified','passed','mapped_road_proximity_verified')
         then 'PASS' else 'REVIEW' end,
    jsonb_build_object('review_id',v_dd.id,'access_status',v_dd.access_status),
    'dd-refresh'
  );

  perform god_mode_ops.set_gate(
    p_deal_id,
    'COMPS_QUALIFIED',
    case when v_dd.id is not null
              and v_dd.comps_status in ('qualified','verified','passed')
         then 'PASS' else 'BLOCKED' end,
    jsonb_build_object('review_id',v_dd.id,'comps_status',v_dd.comps_status),
    'dd-refresh'
  );

  perform god_mode_ops.set_gate(
    p_deal_id,
    'UNDERWRITING',
    case when v_dd.id is not null
              and v_dd.status = 'completed'
              and v_dd.underwriting_status in ('qualified','approved','passed')
              and coalesce(jsonb_typeof(v_dd.fatal_flags)='array' and jsonb_array_length(v_dd.fatal_flags)=0,false)
         then 'PASS' else 'BLOCKED' end,
    jsonb_build_object(
      'review_id',v_dd.id,
      'status',v_dd.status,
      'underwriting_status',v_dd.underwriting_status,
      'fatal_flags',coalesce(v_dd.fatal_flags,'[]'::jsonb)
    ),
    'dd-refresh'
  );

  select exists(
    select 1
    from public.polk_legal_v2 pl
    where pl.parcel_id = v_property.parcel_id
      and nullif(btrim(pl.dscr),'') is not null
  ) into v_has_legal;

  perform god_mode_ops.set_gate(
    p_deal_id,
    'LEGAL_DESCRIPTION',
    case when v_has_legal then 'PASS' else 'BLOCKED' end,
    jsonb_build_object('parcel_id',v_property.parcel_id,'legal_description_present',v_has_legal),
    'legal-refresh'
  );

  perform god_mode_ops.set_gate(
    p_deal_id,
    'ECONOMICS',
    case when v_deal.estimated_value is not null and v_deal.estimated_value > 0
              and v_deal.mao is not null and v_deal.mao > 0
              and v_deal.target_offer is not null and v_deal.target_offer > 0
              and v_deal.target_assignment_fee is not null and v_deal.target_assignment_fee >= 0
              and v_deal.earnest_money is not null and v_deal.earnest_money >= 0
              and v_deal.target_offer <= v_deal.mao
         then 'PASS' else 'BLOCKED' end,
    jsonb_build_object(
      'arv',v_deal.estimated_value,
      'mao',v_deal.mao,
      'target_offer',v_deal.target_offer,
      'target_assignment_fee',v_deal.target_assignment_fee,
      'earnest_money',v_deal.earnest_money
    ),
    'economics-refresh'
  );

  if not exists (
    select 1 from god_mode_ops.deal_gate_status
    where deal_id = p_deal_id and gate_name = 'CONTACT_IDENTITY'
  ) then
    perform god_mode_ops.set_gate(
      p_deal_id,'CONTACT_IDENTITY','BLOCKED',
      jsonb_build_object('reason','awaiting verified seller/owner contact identity'),
      'bootstrap'
    );
  end if;

  if not exists (
    select 1 from god_mode_ops.deal_gate_status
    where deal_id = p_deal_id and gate_name = 'COMPLIANCE'
  ) then
    perform god_mode_ops.set_gate(
      p_deal_id,'COMPLIANCE','BLOCKED',
      jsonb_build_object('reason','awaiting DNC/consent/compliance clearance'),
      'bootstrap'
    );
  end if;

  if not exists (
    select 1 from god_mode_ops.deal_gate_status
    where deal_id = p_deal_id and gate_name = 'SOURCE_RISK'
  ) then
    perform god_mode_ops.set_gate(
      p_deal_id,'SOURCE_RISK','BLOCKED',
      jsonb_build_object('reason','awaiting source-risk clearance or explicit human waiver'),
      'bootstrap'
    );
  end if;
end;
$$;

create or replace function god_mode_ops.required_gates_satisfied(p_deal_id uuid)
returns boolean
language sql
stable
security invoker
set search_path = pg_catalog, god_mode_ops
as $$
  select not exists (
    select 1
    from god_mode_ops.deal_gate_catalog c
    left join god_mode_ops.deal_gate_status s
      on s.deal_id = p_deal_id and s.gate_name = c.gate_name
    where c.required_for_state = 'OFFER_READY'
      and (
        s.gate_name is null
        or s.status not in ('PASS','WAIVED')
        or (s.status = 'WAIVED' and not c.allow_human_waiver)
      )
  );
$$;

create or replace function god_mode_ops.advance_deal_state(
  p_deal_id uuid,
  p_target_state text,
  p_actor_type text default 'system',
  p_actor_id text default null,
  p_reason text default null
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  v_current text;
  v_snapshot_id uuid;
  v_contract_id uuid;
  v_allowed boolean := false;
begin
  select state into v_current
  from god_mode_ops.deal_workflow
  where deal_id = p_deal_id
  for update;

  if v_current is null then
    raise exception 'No workflow for deal %', p_deal_id;
  end if;

  if p_target_state not in ('QUALIFIED','OFFER_READY','HUMAN_APPROVED','CONTRACT_DRAFTED','SIGNATURE_READY','EXECUTED','DISPOSITION_READY','CLOSED','HOLD','REVIEW') then
    raise exception 'Unknown target state %', p_target_state;
  end if;

  if p_target_state in ('HOLD','REVIEW') then
    v_allowed := v_current <> 'CLOSED';
  elsif v_current in ('HOLD','REVIEW') and p_target_state = 'QUALIFIED' then
    v_allowed := true;
  elsif v_current='QUALIFIED' and p_target_state='OFFER_READY' then
    v_allowed := true;
  elsif v_current='OFFER_READY' and p_target_state='HUMAN_APPROVED' then
    v_allowed := true;
  elsif v_current='HUMAN_APPROVED' and p_target_state='CONTRACT_DRAFTED' then
    v_allowed := true;
  elsif v_current='CONTRACT_DRAFTED' and p_target_state='SIGNATURE_READY' then
    v_allowed := true;
  elsif v_current='SIGNATURE_READY' and p_target_state='EXECUTED' then
    v_allowed := true;
  elsif v_current='EXECUTED' and p_target_state='DISPOSITION_READY' then
    v_allowed := true;
  elsif v_current='DISPOSITION_READY' and p_target_state='CLOSED' then
    v_allowed := true;
  end if;

  if not v_allowed then
    raise exception 'Invalid transition % -> %', v_current, p_target_state;
  end if;

  if p_target_state = 'OFFER_READY' then
    perform god_mode_ops.refresh_deal_gates(p_deal_id);
    if not god_mode_ops.required_gates_satisfied(p_deal_id) then
      raise exception 'Required gates are not satisfied for OFFER_READY';
    end if;
    v_snapshot_id := god_mode_ops.create_deal_snapshot(p_deal_id);
  end if;

  if p_target_state = 'HUMAN_APPROVED' then
    select id into v_snapshot_id
    from god_mode_ops.deal_evidence_snapshots
    where deal_id = p_deal_id
    order by snapshot_version desc
    limit 1;

    if v_snapshot_id is null or not exists (
      select 1 from god_mode_ops.deal_approvals
      where deal_id = p_deal_id
        and deal_snapshot_id = v_snapshot_id
        and approval_type = 'OFFER'
        and actor_type = 'human_operator'
        and revoked_at is null
    ) then
      raise exception 'Current deal snapshot lacks transaction-specific human OFFER approval';
    end if;
  end if;

  if p_target_state = 'CONTRACT_DRAFTED' then
    if not exists (
      select 1
      from god_mode_ops.contracts c
      join god_mode_ops.contract_templates t on t.id = c.template_id
      where c.deal_id = p_deal_id
        and c.contract_type = 'ACQUISITION'
        and c.status = 'DRAFTED'
        and c.binding_status = 'NON_BINDING'
        and t.status = 'APPROVED'
    ) then
      raise exception 'No approved-template non-binding acquisition contract draft exists';
    end if;
  end if;

  if p_target_state = 'SIGNATURE_READY' then
    select c.id into v_contract_id
    from god_mode_ops.contracts c
    where c.deal_id = p_deal_id
      and c.contract_type = 'ACQUISITION'
      and c.status in ('DRAFTED','SIGNATURE_READY')
      and c.binding_status = 'NON_BINDING'
    order by c.created_at desc
    limit 1;

    if v_contract_id is null or not exists (
      select 1
      from god_mode_ops.esign_envelopes e
      where e.contract_id = v_contract_id
        and e.status in ('PREPARED','SENT')
        and e.provider <> 'UNCONFIGURED'
    ) then
      raise exception 'Usable e-sign envelope is not prepared';
    end if;

    update god_mode_ops.contracts
    set status='SIGNATURE_READY', updated_at=now()
    where id=v_contract_id;
  end if;

  if p_target_state = 'EXECUTED' then
    select c.id into v_contract_id
    from god_mode_ops.contracts c
    where c.deal_id = p_deal_id
      and c.contract_type='ACQUISITION'
      and c.status='SIGNATURE_READY'
    order by c.created_at desc
    limit 1;

    if v_contract_id is null or not exists (
      select 1
      from god_mode_ops.esign_envelopes e
      where e.contract_id = v_contract_id
        and e.status='COMPLETED'
        and e.executed_document_ref is not null
        and btrim(e.executed_document_ref) <> ''
    ) then
      raise exception 'Executed contract evidence is missing';
    end if;

    update god_mode_ops.contracts
    set status='EXECUTED', binding_status='BINDING', executed_at=now(), updated_at=now()
    where id=v_contract_id;
  end if;

  if p_target_state = 'DISPOSITION_READY' then
    if not exists (
      select 1
      from god_mode_ops.contracts c
      join god_mode_ops.esign_envelopes e on e.contract_id=c.id
      where c.deal_id=p_deal_id
        and c.contract_type='ACQUISITION'
        and c.status='EXECUTED'
        and c.binding_status='BINDING'
        and e.status='COMPLETED'
    ) then
      raise exception 'Executed acquisition contract required before disposition';
    end if;

    if not exists (
      select 1
      from public.deals d
      join public.buyer_matches bm on bm.property_id=d.property_id
      join public.buyers b on b.id=bm.buyer_id
      where d.id=p_deal_id
        and b.active is true
        and b.qualification_status in ('verified_candidate','qualified','verified','approved')
    ) then
      raise exception 'No qualified active buyer match exists';
    end if;
  end if;

  if p_target_state = 'CLOSED' then
    if not exists (
      select 1 from god_mode_ops.disposition_packages
      where deal_id=p_deal_id and status='CLOSED'
    ) then
      raise exception 'Disposition package is not CLOSED';
    end if;
  end if;

  update god_mode_ops.deal_workflow
  set state=p_target_state,
      state_version=state_version+1,
      blocker_code=null,
      blocker_detail=null,
      updated_at=now()
  where deal_id=p_deal_id;

  insert into god_mode_ops.deal_state_history(
    deal_id, from_state, to_state, actor_type, actor_id, reason
  )
  values (
    p_deal_id, v_current, p_target_state,
    coalesce(nullif(btrim(p_actor_type),''),'system'), p_actor_id, p_reason
  );

  return p_target_state;
end;
$$;

create or replace function god_mode_ops.materialize_qualified_deals(p_limit integer default 100)
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  r record;
  v_deal_id uuid;
  v_count integer := 0;
begin
  for r in
    select dd.*, l.id as resolved_lead_id
    from public.due_diligence_reviews dd
    left join lateral (
      select id
      from public.leads
      where property_id=dd.property_id
      order by updated_at desc nulls last, created_at desc nulls last
      limit 1
    ) l on true
    where dd.status='completed'
      and dd.comps_status in ('qualified','verified','passed')
      and dd.underwriting_status in ('qualified','approved','passed')
      and coalesce(jsonb_typeof(dd.fatal_flags)='array' and jsonb_array_length(dd.fatal_flags)=0,false)
      and not exists (
        select 1 from public.deals d
        where d.property_id=dd.property_id
          and coalesce(d.stage,'') not in ('closed','cancelled')
      )
    order by dd.completed_at nulls last, dd.updated_at
    limit greatest(1,least(coalesce(p_limit,100),500))
  loop
    insert into public.deals(property_id,lead_id,stage,estimated_value,exit_strategy)
    select r.property_id, r.resolved_lead_id, 'qualified', p.estimated_market_value, 'wholesale'
    from public.properties p where p.id=r.property_id
    returning id into v_deal_id;

    insert into god_mode_ops.deal_workflow(deal_id,state)
    values(v_deal_id,'QUALIFIED')
    on conflict (deal_id) do nothing;

    perform god_mode_ops.refresh_deal_gates(v_deal_id);
    v_count := v_count + 1;
  end loop;

  return v_count;
end;
$$;

create or replace function god_mode_ops.prepare_disposition_package(p_deal_id uuid)
returns uuid
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  v_contract god_mode_ops.contracts%rowtype;
  v_match public.buyer_matches%rowtype;
  v_buyer public.buyers%rowtype;
  v_payload jsonb;
  v_hash text;
  v_id uuid;
  v_key text;
begin
  select c.* into v_contract
  from god_mode_ops.contracts c
  where c.deal_id=p_deal_id
    and c.contract_type='ACQUISITION'
    and c.status='EXECUTED'
    and c.binding_status='BINDING'
  order by c.executed_at desc nulls last, c.created_at desc
  limit 1;

  if v_contract.id is null then
    raise exception 'Executed acquisition contract required';
  end if;

  select bm.* into v_match
  from public.deals d
  join public.buyer_matches bm on bm.property_id=d.property_id
  join public.buyers b on b.id=bm.buyer_id
  where d.id=p_deal_id
    and b.active is true
    and b.qualification_status in ('verified_candidate','qualified','verified','approved')
  order by bm.match_score desc, bm.created_at
  limit 1;

  if v_match.id is null then
    raise exception 'No qualified buyer match';
  end if;

  select * into v_buyer from public.buyers where id=v_match.buyer_id;

  v_payload := jsonb_build_object(
    'deal_id',p_deal_id,
    'acquisition_contract_id',v_contract.id,
    'buyer_match_id',v_match.id,
    'buyer_id',v_buyer.id,
    'buyer_name',v_buyer.name,
    'buyer_company',v_buyer.company,
    'match_score',v_match.match_score,
    'assignment_requires_human_approval',true
  );
  v_hash := encode(digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');
  v_key := 'disposition:v1:'||p_deal_id::text||':'||v_buyer.id::text||':'||v_contract.document_hash;

  insert into god_mode_ops.disposition_packages(
    deal_id, acquisition_contract_id, buyer_match_id, buyer_id,
    status, package, package_hash, idempotency_key
  )
  values(
    p_deal_id,v_contract.id,v_match.id,v_buyer.id,
    'READY',v_payload,v_hash,v_key
  )
  on conflict (idempotency_key) do update
  set updated_at=now()
  returning id into v_id;

  return v_id;
end;
$$;

create or replace function god_mode_ops.run_transaction_automation(p_limit integer default 100)
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops
as $$
declare
  v_materialized integer := 0;
  v_offer_ready integer := 0;
  v_disposition integer := 0;
  r record;
begin
  v_materialized := god_mode_ops.materialize_qualified_deals(p_limit);

  for r in
    select deal_id,state
    from god_mode_ops.deal_workflow
    where state not in ('CLOSED','HOLD')
    order by updated_at
    limit greatest(1,least(coalesce(p_limit,100),500))
  loop
    begin
      perform god_mode_ops.refresh_deal_gates(r.deal_id);

      if r.state='QUALIFIED' and god_mode_ops.required_gates_satisfied(r.deal_id) then
        perform god_mode_ops.advance_deal_state(r.deal_id,'OFFER_READY','system',null,'all required offer gates passed');
        v_offer_ready := v_offer_ready + 1;
      elsif r.state='EXECUTED' then
        perform god_mode_ops.prepare_disposition_package(r.deal_id);
        perform god_mode_ops.advance_deal_state(r.deal_id,'DISPOSITION_READY','system',null,'executed acquisition contract and qualified buyer match present');
        v_disposition := v_disposition + 1;
      end if;
    exception when others then
      update god_mode_ops.deal_workflow
      set blocker_code='AUTOMATION_BLOCKED',
          blocker_detail=left(sqlerrm,1000),
          updated_at=now()
      where deal_id=r.deal_id;
    end;
  end loop;

  return jsonb_build_object(
    'materialized_deals',v_materialized,
    'advanced_offer_ready',v_offer_ready,
    'advanced_disposition_ready',v_disposition,
    'ran_at',now()
  );
end;
$$;

revoke all on function god_mode_ops.calculate_deal_economics(numeric,numeric,numeric,numeric,numeric,numeric,numeric) from public, anon, authenticated;
revoke all on function god_mode_ops.set_deal_economics(uuid,numeric,numeric,numeric,numeric,numeric,numeric,numeric) from public, anon, authenticated;
revoke all on function god_mode_ops.create_deal_snapshot(uuid) from public, anon, authenticated;
revoke all on function god_mode_ops.set_gate(uuid,text,text,jsonb,text,text,text) from public, anon, authenticated;
revoke all on function god_mode_ops.refresh_deal_gates(uuid) from public, anon, authenticated;
revoke all on function god_mode_ops.required_gates_satisfied(uuid) from public, anon, authenticated;
revoke all on function god_mode_ops.advance_deal_state(uuid,text,text,text,text) from public, anon, authenticated;
revoke all on function god_mode_ops.materialize_qualified_deals(integer) from public, anon, authenticated;
revoke all on function god_mode_ops.prepare_disposition_package(uuid) from public, anon, authenticated;
revoke all on function god_mode_ops.run_transaction_automation(integer) from public, anon, authenticated;

grant execute on function god_mode_ops.calculate_deal_economics(numeric,numeric,numeric,numeric,numeric,numeric,numeric) to service_role;
grant execute on function god_mode_ops.set_deal_economics(uuid,numeric,numeric,numeric,numeric,numeric,numeric,numeric) to service_role;
grant execute on function god_mode_ops.create_deal_snapshot(uuid) to service_role;
grant execute on function god_mode_ops.set_gate(uuid,text,text,jsonb,text,text,text) to service_role;
grant execute on function god_mode_ops.refresh_deal_gates(uuid) to service_role;
grant execute on function god_mode_ops.required_gates_satisfied(uuid) to service_role;
grant execute on function god_mode_ops.advance_deal_state(uuid,text,text,text,text) to service_role;
grant execute on function god_mode_ops.materialize_qualified_deals(integer) to service_role;
grant execute on function god_mode_ops.prepare_disposition_package(uuid) to service_role;
grant execute on function god_mode_ops.run_transaction_automation(integer) to service_role;

do $$
begin
  if not exists (select 1 from cron.job where jobname='god_mode_transaction_worker') then
    perform cron.schedule(
      'god_mode_transaction_worker',
      '*/15 * * * *',
      'select god_mode_ops.run_transaction_automation(100);'
    );
  end if;
end
$$;
