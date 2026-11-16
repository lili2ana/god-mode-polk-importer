#!/usr/bin/env python3
"""Refresh Lee County's private, source-verified master staging screening.

No contact enrichment, public.leads inserts, or outreach occurs here.
Only a staging Supabase connection is permitted. Dry-run is default.
"""
import argparse
import os
import sys
from urllib.parse import unquote, urlsplit

PROJECT = 'ltwqgkcblvtscgldxvjw'
REFRESH_SQL = 'SELECT god_mode_ops.refresh_lee_master_screening()'
STAGING_AUDIT_SQL = '''
SELECT evidence_screening_status,COUNT(*)::int AS parcels,
       COUNT(*) FILTER (WHERE eligible_for_contact)::int AS eligible
FROM god_mode_ops.lee_master_screening
GROUP BY evidence_screening_status
ORDER BY evidence_screening_status
'''
SOURCE_AUDIT_SQL = '''
SELECT evidence_screening_status, COUNT(*)::int AS parcels,
       COUNT(*) FILTER (WHERE evidence_screening_status='QUALIFIED_FOR_CONTACT_ENRICHMENT')::int AS eligible
FROM god_mode_ops.v_lee_master_screening_source
GROUP BY evidence_screening_status
ORDER BY evidence_screening_status
'''
RELEASE_ASSERT_SQL = '''
SELECT COUNT(*)::int FROM god_mode_ops.lee_master_screening
WHERE eligible_for_contact AND (
  NOT geometry_verified OR NOT fema_verified OR NOT nwi_verified
  OR NOT current_tax_verified OR NOT current_delinquency_verified
  OR NOT owner_value_verified OR NOT title_access_verified
  OR evidence_screening_status <> 'QUALIFIED_FOR_CONTACT_ENRICHMENT'
)
'''


def staging_dsn_is_safe(dsn: str) -> bool:
    try:
        parsed = urlsplit(dsn)
        if parsed.scheme not in ('postgresql','postgres') or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        username = unquote(parsed.username or '')
        if host == f'db.{PROJECT}.supabase.co':
            return username == 'postgres'
        if host.endswith('.pooler.supabase.com'):
            return username == f'postgres.{PROJECT}'
        return False
    except (TypeError, ValueError):
        return False


def execute_screening(conn, write: bool) -> tuple[int, list[tuple]]:
    """Refresh only when explicitly authorized, audit full distribution afterward."""
    touched = 0
    with conn.cursor() as cur:
        if write:
            cur.execute(REFRESH_SQL)
            touched = cur.fetchone()[0]
        cur.execute(STAGING_AUDIT_SQL if write else SOURCE_AUDIT_SQL)
        results = cur.fetchall()
        if write:
            cur.execute(RELEASE_ASSERT_SQL)
            leaks = cur.fetchone()[0]
            if leaks:
                conn.rollback()
                raise RuntimeError('BLOCKED: unverified lead in staging screening')
    if write:
        conn.commit()
    else:
        conn.rollback()
    return touched, results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Refresh the private staging table; dry-run otherwise')
    args = parser.parse_args(argv)
    dsn = os.getenv('STAGING_SUPABASE_DB_URL','')
    if not staging_dsn_is_safe(dsn):
        print('BLOCKED: approved STAGING_SUPABASE_DB_URL is required', file=sys.stderr)
        return 2
    import psycopg2
    conn = psycopg2.connect(
        dsn, connect_timeout=12,
        sslmode='verify-full' if os.getenv('PGSSLROOTCERT') else 'require',
    )
    try:
        touched, statuses = execute_screening(conn, args.write)
    finally:
        conn.close()
    print('PRIVATE STAGING ONLY', 'UPDATED' if args.write else 'DRY RUN')
    print('master_rows_refreshed:', touched)
    for name, parcels, eligible in statuses:
        print(name, 'parcels:', parcels, 'eligible:', eligible)
    print('No CRM insert, contact enrichment, or outreach executed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('BLOCKED: master screening could not finish ('+type(exc).__name__+')', file=sys.stderr)
        sys.exit(1)
