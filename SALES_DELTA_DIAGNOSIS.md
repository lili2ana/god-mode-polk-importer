# Sales delta diagnosis — October 5, 2026

Original run 34742320875, job 103683985419, validated 3,031,366 source rows, committed sales_00.csv through sales_23.csv, then failed COPY with ReadOnlySqlTransaction. Live hash-shard grouping independently confirms exactly shards 0–23 present, with zero rows for shards 24–31. Missing historical delta relative to that source count: 760,373 rows.

Current staging classification: 2,039,792 pre-2022; 199,707 eligible recent residential; 31,478 recent non-residential; 16 recent rows on 5 unmatched parcels. No blank, invalid or future dates. All 2,270,993 staged rows accounted for. The shadow matches all 15 fields of eligible staging rows.

The source ZIP was 54,988,670 bytes compressed and 517,703,654 bytes uncompressed. It is not a multi-gigabyte archive. Missing exact keys cannot be derived from absent staging rows; use the accepted archived ZIP, not guessed records. audit_missing_sales_shards.py validates a supplied local archive fingerprint/header/count, then reports missing-shard source/date bounds without downloading, loading or changing the database. Synthetic verification passed shard classification, date selection, fingerprint rejection and count rejection. This is an audit, not a residential repair or proof of completeness.

Next: locate and restore the accepted archive and its SHA-256 manifest; validate recent residential rows from shards 24–31 against current parcel classification; insert only reconciled active rows into the shadow. Resolve the 16 unmatched recent rows. Full-field comparison, comparable-sale qualification, reader tests and durable archive restore are required before reader cutover and staging retirement. Do not rerun the historical truncate-and-load script.
