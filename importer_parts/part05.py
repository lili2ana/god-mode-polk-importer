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


def get_persistent_feed_status(conn, feed_name: str):
    if not table_exists(conn, "god_mode_feed_status"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, source_row_count, stage_row_count, prod_row_count, column_count, "
            "mapping_version, verified_at, github_run_id, commit_sha "
            "FROM public.god_mode_feed_status WHERE feed_name = %s",
            (feed_name,),
        )
        return cur.fetchone()


def persist_feed_status(conn, stage: FeedStage, source_file: str, source_size_bytes: int,
                        source_row_count: int, staged_count: int, prod_count: int,
                        status: str = "verified"):
    run_id = os.environ.get("GITHUB_RUN_ID")
    commit_sha = os.environ.get("GITHUB_SHA")
    mapping_version = f"{stage.name}_v2_{len(stage.columns)}cols"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.god_mode_feed_status
              (feed_name, source_file, source_size_bytes, source_row_count,
               stage_row_count, prod_row_count, column_count, mapping_version,
               status, verified_at, github_run_id, commit_sha, notes, updated_at)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,now())
            ON CONFLICT (feed_name) DO UPDATE SET
              source_file = EXCLUDED.source_file,
              source_size_bytes = EXCLUDED.source_size_bytes,
              source_row_count = EXCLUDED.source_row_count,
              stage_row_count = EXCLUDED.stage_row_count,
              prod_row_count = EXCLUDED.prod_row_count,
              column_count = EXCLUDED.column_count,
              mapping_version = EXCLUDED.mapping_version,
              status = EXCLUDED.status,
              verified_at = EXCLUDED.verified_at,
              github_run_id = EXCLUDED.github_run_id,
              commit_sha = EXCLUDED.commit_sha,
              notes = EXCLUDED.notes,
              updated_at = now()
            """,
            (
                stage.name, source_file, source_size_bytes, source_row_count,
                staged_count, prod_count, len(stage.columns), mapping_version,
                status, run_id, commit_sha,
                "Verified by importer after staging COPY, set-based merge, and post-merge row-count checks.",
            ),
        )
    conn.commit()


def verify_all_stages(conn):
    """Read-only diagnostic: prints stage/production row counts for every
    feed, plus persistent verification state stored in Supabase."""
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

        persistent = get_persistent_feed_status(conn, stage.name)
        if persistent:
            status, source_rows, stored_stage, stored_prod, col_count, mapping_version, verified_at, run_id, commit_sha = persistent
            log.info(
                "  persistent_status=%s source_rows=%s stage_rows=%s prod_rows=%s columns=%s mapping=%s verified_at=%s run=%s sha=%s",
                status, source_rows, stored_stage, stored_prod, col_count, mapping_version,
                verified_at, run_id, commit_sha,
            )
            if status == "verified" and stage_exists and prod_exists:
                if stored_stage != stage_rows or stored_prod != prod_rows:
                    log.warning("  %s persistent verification counts no longer match live tables", stage.name)
        elif stage_rows > 0 or prod_rows > 0:
            log.warning("  %s has data but no persistent Supabase verification record", stage.name)


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
