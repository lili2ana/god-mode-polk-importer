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

  v_hash := encode(extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');

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

  v_hash := encode(extensions.digest(convert_to(coalesce(p_evidence,'{}'::jsonb)::text,'UTF8'),'sha256'),'hex');

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
  v_hash := encode(extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');
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