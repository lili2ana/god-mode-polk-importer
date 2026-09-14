"""Bounded database diagnostic. Prints no credentials or raw database errors."""
import argparse
import json
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from sales_contract import require_writable, validate_target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-probe',action='store_true',help='Create, test and roll back a temporary table only')
    parser.add_argument('--output',type=Path,default=Path('.polk_import/database-readiness.json'))
    args=parser.parse_args()
    evidence={'checked_at':datetime.now(timezone.utc).isoformat(),'status':'BLOCKED','write_probe_passed':False}
    try:
        parts=validate_target(os.environ['SUPABASE_DB_URL'])
        parts.setdefault('sslmode','require')
        parts['connect_timeout']=15
        with closing(psycopg2.connect(**parts)) as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='15s'")
                require_writable(cur)
                cur.execute('SELECT pg_database_size(current_database()), count(*) FROM pg_stat_activity')
                size,connections=cur.fetchone()
                evidence.update(database_bytes=size,active_connections=connections,writable_primary_reported=True)
                if args.write_probe:
                    cur.execute('CREATE TEMP TABLE polk_readiness_probe(value integer) ON COMMIT DROP')
                    cur.execute('INSERT INTO polk_readiness_probe VALUES (1)')
                    cur.execute('SELECT value FROM polk_readiness_probe')
                    if cur.fetchone() != (1,):
                        raise RuntimeError('Temporary write/read verification failed')
                    evidence['write_probe_passed']=True
            conn.rollback()
        evidence['status']='READY'
    except Exception as exc:
        evidence.update(error_type=type(exc).__name__,sqlstate=getattr(exc,'pgcode',None))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence))
    if evidence['status']!='READY':
        raise SystemExit(1)


if __name__=='__main__':
    main()
