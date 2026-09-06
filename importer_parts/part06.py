
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
    transaction is actually writable before any TRUNCATE/COPY/merge begins."""
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


def _legal_streaming_fingerprint(conn, table_name: str):
    """Return a low-temp-space integrity fingerprint for the 8-column legal table.

    This deliberately avoids COUNT(DISTINCT ...) / GROUP BY because those caused
    PostgreSQL to spill to pgsql_tmp and hit the database disk ceiling.  The hash
    aggregate is a sequential scan with constant aggregate state, so it can be
    compared stage-vs-production without a large sort/hash workspace.
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT count(*) AS rows, "
                "count(*) FILTER (WHERE parcel_id IS NULL OR btrim(parcel_id) = '') AS blank_parcel, "
                "count(*) FILTER (WHERE num IS NULL OR btrim(num) = '') AS blank_num, "
                "min(parcel_id), max(parcel_id), "
                "sum(hashtextextended(concat_ws(chr(31), "
                "coalesce(parcel_id,''), coalesce(num,''), coalesce(section,''), "
                "coalesce(township,''), coalesce(range,''), coalesce(sub,''), "
                "coalesce(parcel,''), coalesce(dscr,'')), 0)::numeric) AS fingerprint "
                "FROM {}"
            ).format(sql.Identifier(table_name))
        )
        return cur.fetchone()


def _legal_has_orphans(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT EXISTS ("
                "SELECT 1 FROM {} l "
                "WHERE NOT EXISTS (SELECT 1 FROM polk_parcel_v2 p WHERE p.parcel_id = l.parcel_id) "
                "LIMIT 1)"
            ).format(sql.Identifier(table_name))
        )
        return cur.fetchone()[0]


def run_legal_merge_only(conn) -> dict:
    """Recover legal from the already-loaded, previously validated staging table.

    No FTPS download, parser pass, TRUNCATE, or COPY is repeated.  Stage integrity
    is rechecked with exact row/blank/orphan checks and a streaming content
    fingerprint.  Production must finish at the same exact row count and fingerprint.
    """
    stage = FEEDS["legal"]
    expected_rows = 1_074_337

    if not table_exists(conn, stage.stage_table) or not table_exists(conn, stage.table):
        raise RuntimeError("Legal merge-only requires both legal v2 stage and production tables.")

    staged_count, blank_parcel, blank_num, stage_min, stage_max, stage_fingerprint = \
        _legal_streaming_fingerprint(conn, stage.stage_table)
    stage_orphans = _legal_has_orphans(conn, stage.stage_table)
    conn.rollback()

    log.info(
        "Legal stage revalidation: rows=%d blank_parcel=%d blank_num=%d orphans=%s min=%s max=%s fingerprint=%s",
        staged_count, blank_parcel, blank_num, stage_orphans, stage_min, stage_max, stage_fingerprint,
    )
    if staged_count != expected_rows:
        raise RuntimeError(f"Legal stage row count drift: expected {expected_rows}, got {staged_count}")
    if blank_parcel or blank_num or stage_orphans:
        raise RuntimeError(
            f"Legal stage integrity failure: blank_parcel={blank_parcel}, blank_num={blank_num}, "
            f"orphans={stage_orphans}"
        )

    prod_before = row_count_of(conn, stage.table)
    conn.rollback()
    log.info("Legal production rows before idempotent merge: %d", prod_before)

    upserted = merge_stage_into_production(conn, stage)

    prod_count, prod_blank_parcel, prod_blank_num, prod_min, prod_max, prod_fingerprint = \
        _legal_streaming_fingerprint(conn, stage.table)
    prod_orphans = _legal_has_orphans(conn, stage.table)
    conn.rollback()

    log.info(
        "Legal production verification: rows=%d blank_parcel=%d blank_num=%d orphans=%s min=%s max=%s fingerprint=%s",
        prod_count, prod_blank_parcel, prod_blank_num, prod_orphans, prod_min, prod_max, prod_fingerprint,
    )
    if prod_count != expected_rows:
        raise RuntimeError(f"Legal post-merge row count failed: expected {expected_rows}, got {prod_count}")
    if prod_blank_parcel or prod_blank_num or prod_orphans:
        raise RuntimeError(
            f"Legal post-merge integrity failed: blank_parcel={prod_blank_parcel}, "
            f"blank_num={prod_blank_num}, orphans={prod_orphans}"
        )
    if (prod_min, prod_max, prod_fingerprint) != (stage_min, stage_max, stage_fingerprint):
        raise RuntimeError(
            "Legal post-merge fingerprint mismatch between staging and production — refusing verification."
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
        f"fingerprint={prod_fingerprint}\nsource_of_truth=public.god_mode_feed_status\n"
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
