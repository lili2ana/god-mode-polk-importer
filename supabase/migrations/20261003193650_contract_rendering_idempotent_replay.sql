CREATE OR REPLACE FUNCTION god_mode_ops.render_contract_draft(p_deal_id uuid, p_template_id uuid, p_variables jsonb, p_idempotency_key text)
 RETURNS uuid
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'god_mode_ops', 'extensions'
AS $function$
declare
  v_template god_mode_ops.contract_templates%rowtype;
  v_deal public.deals%rowtype;
  v_property public.properties%rowtype;
  v_state text;
  v_snapshot god_mode_ops.deal_evidence_snapshots%rowtype;
  v_vars jsonb;
  v_rendered text;
  v_key text;
  v_value text;
  v_required text;
  v_hash text;
  v_existing god_mode_ops.contracts%rowtype;
  v_id uuid;
  v_asset_class text;
begin
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' then
    raise exception 'Idempotency key is required';
  end if;

  select * into v_template
  from god_mode_ops.contract_templates
  where id=p_template_id;

  if v_template.id is null
     or v_template.status <> 'APPROVED'
     or (v_template.effective_from is not null and v_template.effective_from > current_date)
     or (v_template.effective_to is not null and v_template.effective_to < current_date) then
    raise exception 'Template is not approved and effective';
  end if;

  if upper(v_template.jurisdiction) not in ('FL','FLORIDA') then
    raise exception 'Template jurisdiction is not Florida';
  end if;

  select * into v_deal
  from public.deals
  where id=p_deal_id;

  select state into v_state
  from god_mode_ops.deal_workflow
  where deal_id=p_deal_id;

  if v_deal.id is null or v_state is null then
    raise exception 'Deal % or workflow not found', p_deal_id;
  end if;

  select * into v_property from public.properties where id=v_deal.property_id;
  v_asset_class := case
    when upper(coalesce(v_property.property_type,'')) like '%LAND%'
      or upper(coalesce(v_property.property_type,'')) like '%VACANT%'
      then 'LAND'
    else 'RESIDENTIAL'
  end;

  if upper(v_template.asset_type) not in ('ANY','ALL',v_asset_class,upper(coalesce(v_property.property_type,''))) then
    raise exception 'Template asset type does not match deal property';
  end if;

  select * into v_existing
  from god_mode_ops.contracts
  where idempotency_key=p_idempotency_key;

  if v_template.contract_type='ACQUISITION'
     and v_state <> 'HUMAN_APPROVED'
     and not (v_state='CONTRACT_DRAFTED' and v_existing.id is not null) then
    raise exception 'Acquisition draft requires HUMAN_APPROVED deal state';
  elsif v_template.contract_type='ASSIGNMENT'
     and v_state <> 'DISPOSITION_READY'
     and not (v_state='SIGNATURE_READY' and v_existing.id is not null) then
    raise exception 'Assignment draft requires DISPOSITION_READY deal state';
  end if;

  select * into v_snapshot
  from god_mode_ops.deal_evidence_snapshots
  where deal_id=p_deal_id
  order by snapshot_version desc
  limit 1;

  if v_snapshot.id is null then
    raise exception 'Deal evidence snapshot is required';
  end if;

  v_vars := coalesce(p_variables,'{}'::jsonb) || jsonb_build_object(
    'deal_id',v_deal.id::text,
    'parcel_id',coalesce(v_property.parcel_id,''),
    'seller_name',coalesce(v_property.owner_name,''),
    'property_address',trim(concat_ws(', ',nullif(v_property.address,''),nullif(v_property.city,''),nullif(v_property.state,''),nullif(v_property.zip,''))),
    'legal_description',coalesce(v_snapshot.snapshot->>'legal_description',''),
    'purchase_price',coalesce(v_deal.target_offer,0)::text,
    'earnest_money',coalesce(v_deal.earnest_money,0)::text,
    'assignment_fee',coalesce(v_deal.target_assignment_fee,0)::text,
    'mao',coalesce(v_deal.mao,0)::text,
    'arv',coalesce(v_deal.estimated_value,0)::text
  );

  foreach v_required in array v_template.required_variables loop
    if not (v_vars ? v_required)
       or nullif(btrim(v_vars->>v_required),'') is null then
      raise exception 'Required contract variable % is missing', v_required;
    end if;
  end loop;

  if v_template.contract_type='ACQUISITION' and nullif(btrim(v_vars->>'legal_description'),'') is null then
    raise exception 'Legal description is required for acquisition contract';
  end if;

  v_rendered := v_template.content;
  for v_key, v_value in select key,value from jsonb_each_text(v_vars)
  loop
    v_rendered := replace(v_rendered,'{{'||v_key||'}}',v_value);
  end loop;

  if v_rendered ~ '\{\{[A-Za-z0-9_.-]+\}\}' then
    raise exception 'Unresolved contract template variables remain';
  end if;

  v_hash := encode(extensions.digest(convert_to(v_rendered,'UTF8'),'sha256'),'hex');

  if v_existing.id is not null then
    if v_existing.deal_id <> p_deal_id
       or v_existing.template_id <> p_template_id
       or v_existing.deal_snapshot_id <> v_snapshot.id
       or v_existing.document_hash <> v_hash then
      raise exception 'Idempotency key conflicts with a different contract intent';
    end if;
    return v_existing.id;
  end if;

  insert into god_mode_ops.contracts(
    deal_id,contract_type,template_id,deal_snapshot_id,status,binding_status,
    rendered_payload,document_hash,idempotency_key
  )
  values(
    p_deal_id,v_template.contract_type,p_template_id,v_snapshot.id,'DRAFTED','NON_BINDING',
    jsonb_build_object(
      'document_text',v_rendered,
      'variables',v_vars,
      'template_key',v_template.template_key,
      'template_version',v_template.version,
      'requires_human_signature',true
    ),
    v_hash,p_idempotency_key
  )
  returning id into v_id;

  if v_template.contract_type='ACQUISITION' then
    perform god_mode_ops.advance_deal_state(
      p_deal_id,'CONTRACT_DRAFTED','system',null,'approved template rendered'
    );
  end if;

  return v_id;
end;
$function$
