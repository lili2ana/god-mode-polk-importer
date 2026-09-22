"""Read a consistent target universe without changing the operational database."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from build_target_tax_snapshot import PROJECT, target_keys
from sales_contract import validate_target

DRIFT_SQL = """
SELECT count(*) FROM public.properties p
FULL JOIN god_mode_ops.target_parcels t ON t.property_id=p.id
WHERE (p.parcel_id IS NOT NULL AND btrim(p.parcel_id)<>'' AND
       (t.property_id IS NULL OR t.parcel_key IS DISTINCT FROM
        regexp_replace(upper(p.parcel_id),'[^A-Z0-9]','','g')))
   OR (t.property_id IS NOT NULL AND
       (p.id IS NULL OR p.parcel_id IS NULL OR btrim(p.parcel_id)=''))
"""


def export(conn):
    conn.set_session(isolation_level='REPEATABLE READ', readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15s'")
            cur.execute(DRIFT_SQL)
            if cur.fetchone()[0] != 0:
                raise ValueError('Target registry differs from current properties; refresh/fix before export')
            cur.execute('SELECT feed_name,destructive_cleanup_enabled FROM god_mode_ops.feed_retention_policy')
            policies = dict(cur.fetchall())
            if set(policies) != {'parcel','owner','legal','sales','parcel_tax','permits'} or any(v is not False for v in policies.values()):
                raise ValueError('Expected six retention policies with cleanup disabled')
            cur.execute('SELECT parcel_key FROM god_mode_ops.target_parcels ORDER BY parcel_key')
            snapshot = {'project_ref': PROJECT, 'captured_at': datetime.now(timezone.utc).isoformat(),
                        'registry_drift': 0, 'parcel_keys': [r[0] for r in cur.fetchall()]}
            target_keys(snapshot)
            return snapshot
    finally:
        conn.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    dsn = os.environ['SUPABASE_DB_URL']
    parts = validate_target(dsn)
    parts.setdefault('sslmode', 'require')
    parts.update(connect_timeout=15, application_name='polk_target_readonly_export')
    conn = psycopg2.connect(**parts)
    try:
        snapshot = export(conn)
    finally:
        conn.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as out:
        json.dump(snapshot, out)
    print(json.dumps({'target_count': len(snapshot['parcel_keys']), 'registry_drift': 0}))


if __name__ == '__main__':
    main()
