
    if staged_count == 0:
        raise RuntimeError(f"Stage '{stage.name}': staging table is empty after load — aborting before merge trust.")

    prod_count = row_count_of(conn, stage.table)
    if prod_count == 0:
        raise RuntimeError(f"Stage '{stage.name}': production table is empty after merge — refusing verification.")

    persist_feed_status(
        conn,
        stage=stage,
        source_file=zip_path.name,
        source_size_bytes=zip_path.stat().st_size,
        source_row_count=staged_count,
        staged_count=staged_count,
        prod_count=prod_count,
        status="verified",
    )

    # Keep a local marker only as a per-run artifact; Supabase is the durable source of truth.
    marker = VERIFIED_MARKER_DIR / f"{stage.name}.verified"
    marker.write_text(
        f"staged_rows={staged_count}\nupserted_rows={upserted}\nprod_rows={prod_count}\n"
        "source_of_truth=public.god_mode_feed_status\n"
    )
    log.info("=== Stage %s verified persistently: staged=%d upserted=%d prod=%d ===",
             stage.name, staged_count, upserted, prod_count)
    return {
        "stage": stage.name,
        "staged": staged_count,
        "upserted": upserted,
        "prod": prod_count,
        "dry_run": False,
    }


def run_stage_direct(stage_name: str, dry_run: bool, conn) -> dict:
    """Legacy row-by-row path (execute_values) — kept for small feeds or debugging."""
    stage = FEEDS[stage_name]
    zip_path = WORKDIR / f"{stage.name}.zip"
    extract_dir = WORKDIR / stage.name
    download_with_retries(stage.url, zip_path)
    extracted_files = extract_zip(zip_path, extract_dir)
    data_file = next((p for p in extracted_files if p.is_file()), None)

    insert_stmt = sql.SQL("INSERT INTO {table} ({cols}) VALUES %s").format(
        table=sql.Identifier(stage.table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in stage.columns),
    )
    batch, total = [], 0
    with conn.cursor() as cur:
        for row in iter_source_rows(stage, data_file):
            batch.append(row)
            if len(batch) >= 5000:
                if not dry_run:
                    execute_values(cur, insert_stmt.as_string(conn), batch, page_size=1000)
                    conn.commit()
                total += len(batch)
                batch = []
        if batch:
            if not dry_run:
                execute_values(cur, insert_stmt.as_string(conn), batch, page_size=1000)
                conn.commit()
            total += len(batch)
    return {"stage": stage.name, "rows": total, "dry_run": dry_run}


def ensure_writable_session(conn):
    """Force a fresh session-level read/write default, then verify the next
    transaction is actually writable before any TRUNCATE/COPY/merge begins.

    Supabase can remain default_transaction_read_only=on after a recovery event
    even when pg_is_in_recovery() is already false.  The importer must fail
    closed rather than discovering that state at the first destructive write.
    """
    previous_autocommit = conn.autocommit
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = off")
            cur.execute(
                "SELECT current_setting('default_transaction_read_only'), pg_is_in_recovery()"
            )
            default_ro, in_recovery = cur.fetchone()
        if default_ro != "off" or in_recovery:
            raise RuntimeError(
                f"Database write preflight failed: default_transaction_read_only={default_ro}, "
                f"pg_is_in_recovery={in_recovery}"
            )
    finally:
        conn.autocommit = previous_autocommit

    # This SELECT starts a brand-new transaction after the session default was
    # changed.  Verify that transaction itself is read/write, then close it so
    # the real write operation starts in a clean transaction.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_setting('transaction_read_only'), "
            "current_setting('default_transaction_read_only'), pg_is_in_recovery()"
        )
        tx_ro, default_ro, in_recovery = cur.fetchone()
    conn.rollback()
    log.info(
        "Write preflight: transaction_read_only=%s default_transaction_read_only=%s recovery=%s",
        tx_ro, default_ro, in_recovery,
    )
    if tx_ro != "off" or default_ro != "off" or in_recovery:
        raise RuntimeError(
            f"Refusing writes: transaction_read_only={tx_ro}, "
            f"default_transaction_read_only={default_ro}, pg_is_in_recovery={in_recovery}"
        )


def run_legal_merge_only(conn) -> dict:
    """Recover a legal import from an already-validated staging table.

    This intentionally skips FTPS download, parsing, TRUNCATE and COPY.  It first
    re-validates the live staging table against the exact hardened-parser
    acceptance bar, then performs the idempotent committed prefix-chunk merge.
    """
    stage = FEEDS["legal"]
    expected_rows = 1_074_337

    if not table_exists(conn, stage.stage_table) or not table_exists(conn, stage.table):
        raise RuntimeError("Legal merge-only requires both legal v2 stage and production tables.")

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT count(*) AS rows, "
                "count(*) FILTER (WHERE parcel_id IS NULL OR btrim(parcel_id) = '') AS blank_parcel, "
                "count(*) FILTER (WHERE num IS NULL OR btrim(num) = '') AS blank_num, "
                "count(DISTINCT (parcel_id, num)) AS distinct_keys "
                "FROM {}"
            ).format(sql.Identifier(stage.stage_table))
        )
        staged_count, blank_parcel, blank_num, distinct_keys = cur.fetchone()
        cur.execute(
            sql.SQL(
                "SELECT count(DISTINCT l.parcel_id) "
                "FROM {} l LEFT JOIN polk_parcel_v2 p USING (parcel_id) "
                "WHERE p.parcel_id IS NULL"
            ).format(sql.Identifier(stage.stage_table))
        )
        orphan_parcels = cur.fetchone()[0]
    conn.rollback()

    log.info(
        "Legal stage revalidation: rows=%d distinct_keys=%d blank_parcel=%d blank_num=%d orphan_parcels=%d",
        staged_count, distinct_keys, blank_parcel, blank_num, orphan_parcels,
    )
    if staged_count != expected_rows:
        raise RuntimeError(f"Legal stage row count drift: expected {expected_rows}, got {staged_count}")
    if distinct_keys != expected_rows:
        raise RuntimeError(
            f"Legal stage composite-key drift: expected {expected_rows} unique keys, got {distinct_keys}"
        )
    if blank_parcel or blank_num or orphan_parcels:
        raise RuntimeError(
            f"Legal stage integrity failure: blank_parcel={blank_parcel}, blank_num={blank_num}, "
            f"orphan_parcels={orphan_parcels}"
        )

    prod_before = row_count_of(conn, stage.table)
    conn.rollback()
    log.info("Legal production rows before idempotent merge: %d", prod_before)

    upserted = merge_stage_into_production(conn, stage)
    prod_count = row_count_of(conn, stage.table)
    conn.rollback()

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT count(DISTINCT (parcel_id, num)) FROM {}").format(
                sql.Identifier(stage.table)
            )
        )
        prod_distinct_keys = cur.fetchone()[0]
        cur.execute(
            sql.SQL(
                "SELECT count(DISTINCT l.parcel_id) "
                "FROM {} l LEFT JOIN polk_parcel_v2 p USING (parcel_id) "
                "WHERE p.parcel_id IS NULL"
            ).format(sql.Identifier(stage.table))
        )
        prod_orphans = cur.fetchone()[0]
    conn.rollback()

    if prod_count != expected_rows or prod_distinct_keys != expected_rows or prod_orphans != 0:
        raise RuntimeError(
            f"Legal post-merge verification failed: prod={prod_count}, distinct_keys={prod_distinct_keys}, "
            f"orphans={prod_orphans}; expected {expected_rows}/{expected_rows}/0"
        )

    persist_feed_status(
        conn,
        stage=stage,
        source_file="ftp_legal.zip",
        source_size_bytes=None,
        source_row_count=expected_rows,
        staged_count=staged_count,
        prod_count=prod_count,
        status="verified",
    )
    marker = VERIFIED_MARKER_DIR / "legal.verified"
    marker.write_text(
        f"staged_rows={staged_count}\nupserted_rows={upserted}\nprod_rows={prod_count}\n"
        "source_of_truth=public.god_mode_feed_status\n"
    )
    log.info(
        "=== Legal merge-only verified persistently: staged=%d upserted=%d prod=%d ===",
        staged_count, upserted, prod_count,
    )
    return {"stage": "legal", "staged": staged_count, "upserted": upserted, "prod": prod_count}


def persistent_full_sequence_verified(conn) -> bool:
    if not table_exists(conn, "god_mode_feed_status"):
        return False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT feed_name, status FROM public.god_mode_feed_status WHERE feed_name = ANY(%s)",
            (SEQUENCE,),
        )
        statuses = {name: status for name, status in cur.fetchall()}
    return all(statuses.get(name) == "verified" for name in SEQUENCE)


def main():
    parser = argparse.ArgumentParser(description="Polk Bulk Importer — production runner")
    parser.add_argument("--only", choices=SEQUENCE)
    parser.add_argument("--resume-from", choices=SEQUENCE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--partitions", type=int, default=DEFAULT_PARTITIONS)
    parser.add_argument("--mode", choices=["staging", "direct"], default="staging")
    parser.add_argument("--merge-only", choices=["legal"],
                         help="Use an already-validated staging table and perform only the production merge + verification.")
    parser.add_argument("--verify-only", action="store_true",
                         help="Read-only: print stage/production row counts and persistent Supabase verification state, then exit.")
    args = parser.parse_args()

    if args.verify_only:
        conn = get_db_connection()
        try:
            verify_all_stages(conn)
        finally:
            conn.close()
        return

    if args.merge_only:
        conn = get_db_connection()
        try:
            print_connection_identity(conn)
            conn.rollback()
            ensure_writable_session(conn)
            result = run_legal_merge_only(conn)
            all_verified = persistent_full_sequence_verified(conn)
            conn.rollback()
            log.info("Done. Result: %s", result)
            log.info(
                "Full-sequence persistent verification: %s — %s",
                all_verified,
                "seller outreach gate may be reviewed" if all_verified else "seller outreach must stay disabled",
            )
        except Exception:
            log.exception("Merge-only sequence aborted.")
            sys.exit(1)
        finally:
            conn.close()
        return

    if args.only:
        stages_to_run = [args.only]
    elif args.resume_from:
        start = SEQUENCE.index(args.resume_from)
        stages_to_run = SEQUENCE[start:]
    else:
        stages_to_run = SEQUENCE

    conn = None if (args.dry_run and args.mode == "staging") else get_db_connection()
    if conn is not None:
        print_connection_identity(conn)
        conn.rollback()
        if not args.dry_run:
            ensure_writable_session(conn)

    try:
        results = {}
        for stage_name in stages_to_run:
            if args.mode == "staging":
                results[stage_name] = run_stage_staging(stage_name, args.partitions, args.dry_run, conn)
            else:
                results[stage_name] = run_stage_direct(stage_name, args.dry_run, conn)

        all_verified = False
        if conn is not None:
            all_verified = persistent_full_sequence_verified(conn)
    except Exception:
        log.exception("Import sequence aborted.")
        sys.exit(1)
    finally:
        if conn is not None:
            conn.close()

    log.info("Done. Results: %s", results)
    log.info(
        "Full-sequence persistent verification: %s — %s",
        all_verified,
        "seller outreach gate may be reviewed" if all_verified else "seller outreach must stay disabled",
    )


if __name__ == "__main__":
    main()
