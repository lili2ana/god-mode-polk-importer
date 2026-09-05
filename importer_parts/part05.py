def table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s)",
            (table,),
        )
        return cur.fetchone()[0]


def print_connection_identity(conn):
    """Print exactly which database/host we're actually talking to — the
    fastest way to catch a '0 rows because wrong project' problem."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, inet_server_addr()")
        db, user, host = cur.fetchone()
    log.info("Connected as %s@%s to database '%s'", user, host, db)


def verify_all_stages(conn):
    """Read-only diagnostic: prints stage/production row counts for every
    feed, and flags missing tables or empty tables explicitly. Run this
    FIRST whenever counts look wrong instead of re-running the full import."""
    print_connection_identity(conn)
    log.info("%-12s %-28s %10s   %-28s %10s", "STAGE", "STAGE TABLE", "ROWS", "PROD TABLE", "ROWS")
    for stage_name in SEQUENCE:
        stage = FEEDS[stage_name]
        stage_exists = table_exists(conn, stage.stage_table)
        prod_exists = table_exists(conn, stage.table)
        stage_rows = row_count_of(conn, stage.stage_table) if stage_exists else -1
        prod_rows = row_count_of(conn, stage.table) if prod_exists else -1
        stage_label = stage.stage_table if stage_exists else f"{stage.stage_table} (MISSING)"
        prod_label = stage.table if prod_exists else f"{stage.table} (MISSING)"
        log.info("%-12s %-28s %10s   %-28s %10s", stage.name, stage_label,
                  stage_rows if stage_exists else "n/a", prod_label, prod_rows if prod_exists else "n/a")

        marker = VERIFIED_MARKER_DIR / f"{stage.name}.verified"
        if not marker.exists() and (stage_rows > 0 or prod_rows > 0):
            log.warning("  %s has data but no local .verified marker — "
                        "was this loaded by a different run/host?", stage.name)
        if marker.exists() and stage_rows == 0:
            log.warning("  %s has a .verified marker but stage table reads 0 now — "
                        "was it truncated by a later run without reloading?", stage.name)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def get_db_connection():
    raw = os.environ.get("SUPABASE_DB_URL", "")
    db_url = raw.strip().strip('"').strip("'")
    if db_url.startswith("SUPABASE_DB_URL="):
        db_url = db_url.split("=", 1)[1].strip().strip('"').strip("'")
    if not db_url:
        raise RuntimeError("SUPABASE_DB_URL is not set (direct Postgres connection string).")
    if not (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        raise RuntimeError(
            "SUPABASE_DB_URL must be the full Postgres connection URI from Supabase, beginning with postgresql:// or postgres://."
        )
    if psycopg2 is None:
        raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
    return psycopg2.connect(db_url)


def run_stage_staging(stage_name: str, num_partitions: int, dry_run: bool, conn) -> dict:
    stage = FEEDS[stage_name]
    log.info("=== Stage: %s -> stage table %s -> production %s ===", stage.name, stage.stage_table, stage.table)

    if "REPLACE_WITH" in stage.url:
        raise RuntimeError(f"Stage '{stage.name}' still has a placeholder URL — fill in FEEDS[...].url first.")
    if not stage.mapping_verified and not dry_run:
        raise RuntimeError(
            f"Stage '{stage.name}' mapping is not verified against the live Polk source header. "
            "Refusing production writes until columns/conflict_key are confirmed."
        )

    zip_path = WORKDIR / f"{stage.name}.zip"
    extract_dir = WORKDIR / stage.name
    download_with_retries(stage.url, zip_path)
    extracted_files = extract_zip(zip_path, extract_dir)
    data_file = next((p for p in extracted_files if p.is_file()), None)
    if data_file is None:
        raise RuntimeError(f"No data file found after extracting {zip_path}")

    log.info("Streaming + partitioning %s (%.1f MB)", data_file.name, data_file.stat().st_size / 1e6)
    shard_paths = bucket_into_partitions(stage, data_file, num_partitions)

    if dry_run:
        total_rows = sum(sum(1 for _ in open(p)) for p in shard_paths)
        log.info("Dry run — would load %d rows into %s, nothing written.", total_rows, stage.stage_table)
        return {"stage": stage.name, "rows": total_rows, "dry_run": True}

    truncate_stage_table(conn, stage)
    copy_shards_into_stage(conn, stage, shard_paths)
    staged_count = row_count_of(conn, stage.stage_table)
    upserted = merge_stage_into_production(conn, stage)
