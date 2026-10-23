
alter function god_mode_ops.create_deal_snapshot(uuid)
  set search_path = pg_catalog, public, god_mode_ops, extensions;
alter function god_mode_ops.set_gate(uuid,text,text,jsonb,text,text,text)
  set search_path = pg_catalog, god_mode_ops, extensions;
alter function god_mode_ops.prepare_disposition_package(uuid)
  set search_path = pg_catalog, public, god_mode_ops, extensions;
