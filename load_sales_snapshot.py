#!/usr/bin/env python3
"""Accepted-snapshot sales loader. Default mode only validates the source locally."""
import argparse
import csv
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from probe_feed_evidence import download
from sales_contract import COLUMNS, prepare, reconcile, require_writable, validate_target

FIELDS = ','.join(COLUMNS)
LOCK_ID = 732190401
SELECT_TYPED = """btrim(parcel_id),nullif(sale_id,''),ln_num::integer,
case when nullif(btrim(saledt),'') is null then null else to_date(btrim(saledt),'MM/DD/YYYY') end,
nullif(btrim(price),'')::numeric,""" + ','.join(f"nullif({c},'')" for c in COLUMNS[5:])


def scan(conn, table):
    # Table names are constants chosen by this module, never CLI/user input.
    with conn.cursor(name='polk_evidence_scan') as cursor:
        cursor.itersize = 10000
        cursor.execute(f'SELECT {FIELDS} FROM public.{table}')
        yield from cursor


def reconcile_table(conn, local, table, production=False):
    # Close server-side cursors before the surrounding transaction rolls back
    # when a field mismatch raises midway through a streaming comparison.
    with closing(scan(conn, table)) as rows:
        return reconcile(local, rows, production=production)


def load(conn, local, manifest, workdir):
    with conn.cursor() as cursor:
        require_writable(cursor)
        cursor.execute('SELECT pg_try_advisory_lock(%s)', (LOCK_ID,))
        if not cursor.fetchone()[0]:
            raise RuntimeError('Another sales loader holds the session lock')
    conn.commit()
    try:
        # Refuse to discard legacy staging. Reuse only rows proved identical to source.
        with conn:
            with conn.cursor() as cursor:
                require_writable(cursor)
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute('LOCK TABLE public.polk_sales_stage_v2 IN SHARE ROW EXCLUSIVE MODE')
            existing = reconcile_table(conn, local, 'polk_sales_stage_v2')
        print(f'resumable_stage_rows={existing}', flush=True)
        for shard in range(manifest['partitions']):
            shard_path = workdir / f'missing-{shard:02d}.csv'
            count = 0
            with shard_path.open('w', encoding='utf-8', newline='') as out:
                writer = csv.writer(out)
                for (payload,) in local.execute('SELECT payload FROM expected e WHERE shard=? AND NOT EXISTS (SELECT 1 FROM seen s WHERE s.parcel=e.parcel AND s.line=e.line)', (shard,)):
                    writer.writerow(json.loads(payload))
                    count += 1
            if not count:
                continue
            with conn:
                with conn.cursor() as cursor, shard_path.open(encoding='utf-8', newline='') as stream:
                    require_writable(cursor)
                    cursor.execute("SET LOCAL lock_timeout = '5s'")
                    cursor.execute('LOCK TABLE public.polk_sales_stage_v2 IN SHARE ROW EXCLUSIVE MODE')
                    cursor.copy_expert(f'COPY public.polk_sales_stage_v2 ({FIELDS}) FROM STDIN WITH (FORMAT csv)', stream)
            print(f'committed_shard={shard} rows={count}', flush=True)
        # Source equality, production merge, equality check, marker and cleanup
        # either all commit or all roll back. Prefixes do not commit separately.
        with conn:
            with conn.cursor() as cursor:
                require_writable(cursor)
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '30min'")
                cursor.execute('LOCK TABLE public.polk_sales_stage_v2 IN ACCESS EXCLUSIVE MODE')
                cursor.execute('LOCK TABLE public.polk_sales_v2 IN EXCLUSIVE MODE')
                cursor.execute('LOCK TABLE public.god_mode_feed_status IN SHARE ROW EXCLUSIVE MODE')
            staged = reconcile_table(conn, local, 'polk_sales_stage_v2')
            if staged != manifest['rows']:
                raise ValueError('Staging coverage is incomplete; production untouched')
            updates = ','.join(f'{c}=excluded.{c}' for c in COLUMNS if c not in ('parcel_id','ln_num'))
            with conn.cursor() as cursor:
                cursor.execute('SELECT DISTINCT left(btrim(parcel_id),3) FROM public.polk_sales_stage_v2 ORDER BY 1')
                prefixes = [row[0] for row in cursor.fetchall()]
                for prefix in prefixes:
                    cursor.execute(f'''INSERT INTO public.polk_sales_v2 ({FIELDS},updated_at)
                        SELECT {SELECT_TYPED},now() FROM public.polk_sales_stage_v2
                        WHERE left(btrim(parcel_id),3)=%s
                        ON CONFLICT (parcel_id,ln_num) DO UPDATE SET {updates},updated_at=now()''', (prefix,))
                    print(f'pending_prefix={prefix} rows={cursor.rowcount}', flush=True)
            production = reconcile_table(conn, local, 'polk_sales_v2', production=True)
            if production != manifest['rows']:
                raise ValueError('Production coverage is incomplete; publication rolled back')
            with conn.cursor() as cursor:
                cursor.execute('''INSERT INTO public.god_mode_feed_status
                    (feed_name,source_file,source_size_bytes,source_row_count,stage_row_count,prod_row_count,column_count,mapping_version,status,verified_at,github_run_id,commit_sha,notes,updated_at)
                    VALUES ('sales','ftp_sales.zip',%s,%s,%s,%s,15,'sales_v2_15cols_parcel_ln_key','verified',now(),%s,%s,%s,now())
                    ON CONFLICT (feed_name) DO UPDATE SET source_file=excluded.source_file,
                    source_size_bytes=excluded.source_size_bytes,source_row_count=excluded.source_row_count,
                    stage_row_count=excluded.stage_row_count,prod_row_count=excluded.prod_row_count,
                    column_count=excluded.column_count,mapping_version=excluded.mapping_version,status=excluded.status,
                    verified_at=excluded.verified_at,github_run_id=excluded.github_run_id,commit_sha=excluded.commit_sha,
                    notes=excluded.notes,updated_at=excluded.updated_at''',
                    (manifest['archive_bytes'],manifest['rows'],staged,production,os.getenv('GITHUB_RUN_ID'),
                     os.getenv('GITHUB_SHA'),json.dumps({'sha256':manifest['sha256'],'reconciliation':'all keys and all 15 fields','stage_count_before_cleanup':staged})))
                cursor.execute('TRUNCATE public.polk_sales_stage_v2')
        manifest['production_verified'] = True
        print('feed_status=verified publication_committed=true', flush=True)
    finally:
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', (LOCK_ID,))
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--expected-rows', type=int, default=3031366)
    parser.add_argument('--partitions', type=int, default=32)
    parser.add_argument('--accepted-sha256')
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--capacity-reviewed', action='store_true', help='Require infrastructure review before bulk publication')
    parser.add_argument('--workdir', type=Path, default=Path('.polk_import/sales'))
    parser.add_argument('--manifest-output', type=Path)
    args = parser.parse_args()
    if args.load:
        parser.error('Countywide production loading is disabled; use the lean shadow pipeline')
    workdir = args.workdir / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    workdir.mkdir(parents=True)
    manifest = {'production_verified':False,'status':'BLOCKED'}
    try:
        archive = args.zip or workdir / 'ftp_sales.zip'
        if not args.zip:
            manifest['source'] = download('sales', archive)
        manifest.update(prepare(archive, workdir/'source.sqlite', args.expected_rows, args.partitions))
        if args.accepted_sha256 and manifest['sha256'] != args.accepted_sha256:
            raise ValueError('Source fingerprint differs from accepted manifest')
        manifest['status'] = 'READY'
        print(f'source_validated=true rows={manifest["rows"]} sha256={manifest["sha256"]}', flush=True)
    except Exception as exc:
        manifest['error_type'] = type(exc).__name__
        manifest['sqlstate'] = getattr(exc,'pgcode',None)
        if isinstance(exc, (ValueError, csv.Error)):
            manifest['validation_error'] = str(exc)
        print(f'BLOCKED: {type(exc).__name__}; SQLSTATE={manifest["sqlstate"]}', flush=True)
        raise SystemExit(1) from None
    finally:
        (workdir/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        if args.manifest_output:
            args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_output.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(f'evidence={workdir / "manifest.json"}',flush=True)


if __name__ == '__main__':
    main()
