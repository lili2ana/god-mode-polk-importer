
    if staged_count == 0:
        raise RuntimeError(f"Stage '{stage.name}': staging table is empty after load — aborting before merge trust.")

    marker = VERIFIED_MARKER_DIR / f"{stage.name}.verified"
    marker.write_text(f"staged_rows={staged_count}\nupserted_rows={upserted}\n")
    log.info("=== Stage %s verified: staged=%d upserted=%d ===", stage.name, staged_count, upserted)
    return {"stage": stage.name, "staged": staged_count, "upserted": upserted, "dry_run": False}


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


def main():
    parser = argparse.ArgumentParser(description="Polk Bulk Importer — production runner")
    parser.add_argument("--only", choices=SEQUENCE)
    parser.add_argument("--resume-from", choices=SEQUENCE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--partitions", type=int, default=DEFAULT_PARTITIONS)
    parser.add_argument("--mode", choices=["staging", "direct"], default="staging")
    parser.add_argument("--verify-only", action="store_true",
                         help="Read-only: print stage/production row counts for every feed and exit. Run this first if counts look wrong.")
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
    except Exception:
        log.exception("Import sequence aborted.")
        sys.exit(1)
    finally:
        if conn is not None:
            conn.close()

    all_verified = all((VERIFIED_MARKER_DIR / f"{s}.verified").exists() for s in SEQUENCE)
    log.info("Done. Results: %s", results)
    log.info(
        "Full-sequence verified: %s — %s",
        all_verified,
        "seller outreach gate may be reviewed" if all_verified else "seller outreach must stay disabled",
    )


if __name__ == "__main__":
    main()
