

def bucket_into_partitions(stage: FeedStage, data_file: Path, num_partitions: int) -> list[Path]:
    """Single pass over the source file, fanning rows out into N on-disk
    CSV shards keyed by parcel_id hash. Returns the shard paths."""
    partition_dir = WORKDIR / stage.name / "partitions"
    partition_dir.mkdir(parents=True, exist_ok=True)

    shard_paths = [partition_dir / f"part_{i:03d}.csv" for i in range(num_partitions)]
    shard_files = [open(p, "w", newline="", encoding="utf-8") for p in shard_paths]
    shard_writers = [csv.writer(f) for f in shard_files]

    row_count = 0
    try:
        for row in iter_source_rows(stage, data_file):
            parcel_id = row[stage.columns.index("parcel_id")]
            idx = partition_for(parcel_id, num_partitions)
            shard_writers[idx].writerow(row)
            row_count += 1
    finally:
        for f in shard_files:
            f.close()

    log.info("Partitioned %d rows across %d shards for %s", row_count, num_partitions, stage.name)
    return shard_paths


# --------------------------------------------------------------------------
# Staging load (COPY) + set-based merge into production
# --------------------------------------------------------------------------

def truncate_stage_table(conn, stage: FeedStage):
    with conn.cursor() as cur:
        cur.execute(sql.SQL("TRUNCATE TABLE {}").format(sql.Identifier(stage.stage_table)))
    conn.commit()
    log.info("Truncated %s", stage.stage_table)


def copy_shards_into_stage(conn, stage: FeedStage, shard_paths: list[Path]) -> int:
    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in stage.columns)
    copy_sql = sql.SQL("COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv)").format(
        table=sql.Identifier(stage.stage_table), cols=cols_sql
    )
    total = 0
    with conn.cursor() as cur:
        for shard_path in shard_paths:
            if shard_path.stat().st_size == 0:
                continue
            with open(shard_path, "r", encoding="utf-8") as f:
                cur.copy_expert(copy_sql.as_string(conn), f)
            conn.commit()
            total += 1
            log.info("  copied shard %s into %s", shard_path.name, stage.stage_table)
    return total


def _build_merge_sql(stage: FeedStage, where_sql=None):
    update_cols = [c for c in stage.columns if c not in stage.conflict_key]
    base = sql.SQL(
        "INSERT INTO {table} ({cols}) "
        "SELECT {cols} FROM {stage_table} {where_clause} "
        "ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
    )
    return base.format(
        table=sql.Identifier(stage.table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in stage.columns),
        stage_table=sql.Identifier(stage.stage_table),
        where_clause=where_sql if where_sql is not None else sql.SQL(""),
        conflict=sql.SQL(", ").join(sql.Identifier(c) for c in stage.conflict_key),
        updates=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in update_cols
        ),
    )


def _merge_legal_in_prefix_chunks(conn, stage: FeedStage) -> int:
    """Merge the million-row legal feed in small committed parcel-id ranges.

    A single INSERT..SELECT over the full legal stage caused the Supabase database
    connection to be terminated during production merge. Legal parcel IDs are numeric
    coded strings, so use distinct 3-digit prefixes as non-overlapping lexicographic
    ranges. Each range commits independently, bounding statement memory/WAL pressure
    while preserving idempotent ON CONFLICT behavior.
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT DISTINCT left(parcel_id, 3) FROM {} ORDER BY 1").format(
                sql.Identifier(stage.stage_table)
            )
        )
        prefixes = [row[0] for row in cur.fetchall() if row[0]]

    total = 0
    for prefix in prefixes:
        if not prefix.isdigit():
            raise ValueError(f"legal merge prefix is not numeric: {prefix!r}")
        upper = str(int(prefix) + 1).zfill(len(prefix))
        where_clause = sql.SQL("WHERE parcel_id >= %s AND parcel_id < %s")
        insert_sql = _build_merge_sql(stage, where_clause)
        with conn.cursor() as cur:
            cur.execute(insert_sql, (prefix, upper))
            affected = cur.rowcount
        conn.commit()
        total += affected
        log.info("  merged legal parcel prefix %s: %d rows", prefix, affected)

    log.info("Merged %s -> %s in %d prefix chunks: %d rows upserted",
             stage.stage_table, stage.table, len(prefixes), total)
    return total


def merge_stage_into_production(conn, stage: FeedStage) -> int:
    """Set-based INSERT ... ON CONFLICT DO UPDATE from stage into production.

    Most feeds use one statement. Legal uses committed prefix chunks because its
    1M+ row merge exceeded the stable resource envelope of a single statement.
    """
    if stage.name == "legal":
        return _merge_legal_in_prefix_chunks(conn, stage)

    insert_sql = _build_merge_sql(stage)
    with conn.cursor() as cur:
        cur.execute(insert_sql)
        affected = cur.rowcount
    conn.commit()
    log.info("Merged %s -> %s: %d rows upserted", stage.stage_table, stage.table, affected)
    return affected


def row_count_of(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        return cur.fetchone()[0]

