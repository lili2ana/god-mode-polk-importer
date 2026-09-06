
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
