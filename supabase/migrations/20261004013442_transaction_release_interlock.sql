-- The installed core is not a released transaction service. These entry points
-- must remain blocked until the authenticated approval and provider boundaries
-- are implemented and independently verified. No runtime flag can release them.
set local lock_timeout = '3s';
set local statement_timeout = '30s';

create or replace function god_mode_ops.require_transaction_release()
returns void language plpgsql security invoker set search_path = '' as $$
begin
  raise exception using errcode = '55000',
    message = 'TRANSACTION_RELEASE_REQUIRED';
end;
$$;
revoke all on function god_mode_ops.require_transaction_release() from public, anon, authenticated;
grant execute on function god_mode_ops.require_transaction_release() to service_role;

-- Guard direct calls as well as cron. Preserve the original bodies for audit.
do $guard$
declare f record; definition text;
begin
  for f in
    select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='god_mode_ops' and p.proname in
      ('set_deal_economics','create_deal_snapshot','set_gate',
       'refresh_deal_gates','advance_deal_state','materialize_qualified_deals',
       'prepare_disposition_package','render_contract_draft','prepare_esign_intent')
  loop
    definition := pg_get_functiondef(f.oid);
    if position('perform god_mode_ops.require_transaction_release();' in definition)=0 then
      if position(E'begin\n' in definition)=0 then
        raise exception 'Transaction function layout changed: %',f.oid;
      end if;
      definition := overlay(definition placing
        E'begin\n  perform god_mode_ops.require_transaction_release();\n'
        from position(E'begin\n' in definition) for length(E'begin\n'));
      execute definition;
    end if;
  end loop;
end;
$guard$;

create or replace function god_mode_ops.block_unreleased_transaction_write()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
  perform god_mode_ops.require_transaction_release();
  return null;
end;
$$;
revoke all on function god_mode_ops.block_unreleased_transaction_write() from public, anon, authenticated;

-- A service key must not bypass the entry-point guard by writing a status label.
do $triggers$
declare table_name text;
begin
  foreach table_name in array array[
    'deal_gate_catalog','deal_workflow','deal_gate_status','deal_evidence_snapshots',
    'contract_templates','contracts','deal_approvals','esign_envelopes',
    'disposition_packages','buyer_message_outbox','deal_state_history',
    'dd_land_comp_qualification']
  loop
    execute format('create trigger transaction_release_required before insert or update or delete or truncate on god_mode_ops.%I for each statement execute function god_mode_ops.block_unreleased_transaction_write()',table_name);
    execute format('revoke insert,update,delete,truncate,references,trigger on god_mode_ops.%I from service_role',table_name);
  end loop;
end;
$triggers$;

-- Keep the schedule observable and non-mutating. SQL success means the check
-- ran; the returned BLOCKED status expressly does not mean business success.
create or replace function god_mode_ops.run_transaction_automation(p_limit integer default 100)
returns jsonb language sql security invoker set search_path = '' as $$
  select jsonb_build_object('status','BLOCKED',
    'blocker_code','TRANSACTION_RELEASE_REQUIRED',
    'materialized_deals',0,'advanced_offer_ready',0,
    'advanced_disposition_ready',0,'ran_at',now());
$$;
revoke all on function god_mode_ops.run_transaction_automation(integer) from public, anon, authenticated;
grant execute on function god_mode_ops.run_transaction_automation(integer) to service_role;

-- Recorded-sale filtering is not verified comparable-sale qualification.
-- The original implementation remains in migration history for review.
-- Preserve all existing DD rows; this scheduled check writes no valuations.
create or replace function god_mode_ops.refresh_land_comp_qualification()
returns jsonb language sql security invoker set search_path = '' as $$
  select jsonb_build_object('status','BLOCKED',
    'blocker_code','COMPARABILITY_RELEASE_REQUIRED','processed',0,'qualified',0,
    'eligible_for_automated_underwriting',false,'ran_at',now());
$$;
revoke all on function god_mode_ops.refresh_land_comp_qualification() from public, anon, authenticated;
grant execute on function god_mode_ops.refresh_land_comp_qualification() to service_role;
