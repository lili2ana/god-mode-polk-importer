

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


def merge_stage_into_production(conn, stage: FeedStage) -> int:
    """Set-based INSERT ... ON CONFLICT DO UPDATE from stage table into
    production table. One statement for the whole stage — no per-row
    round trips."""
    update_cols = [c for c in stage.columns if c not in stage.conflict_key]

    insert_sql = sql.SQL(
        "INSERT INTO {table} ({cols}) "
        "SELECT {cols} FROM {stage_table} "
        "ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
    ).format(
        table=sql.Identifier(stage.table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in stage.columns),
        stage_table=sql.Identifier(stage.stage_table),
        conflict=sql.SQL(", ").join(sql.Identifier(c) for c in stage.conflict_key),
        updates=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in update_cols
        ),
    )

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

