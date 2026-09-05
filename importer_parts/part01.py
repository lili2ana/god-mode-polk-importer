#!/usr/bin/env python3
"""
Polk Bulk Importer — production runner (staging + set-based merge)
====================================================================

Sequence (unchanged): owner -> parcel -> sales -> legal -> parcel-tax -> permits

For each stage this version:
    1. Downloads + streams-decompresses the feed (disk-backed, safe for the
       113M+ char legal file).
    2. Buckets rows into N parcel-prefix partitions (stable hash of
       parcel_id) into on-disk CSV shards — this is the "partitioned"
       loading your production spec calls for, and it lets a failure
       partway through only cost you the partitions not yet copied.
    3. Bulk-loads each partition into its `<table>_stage` table via
       COPY FROM STDIN (far faster than row-by-row INSERT for a 94 MB+
       file).
    4. Runs one set-based INSERT ... ON CONFLICT DO UPDATE from staging
       into the production table (the "set-based update" step).
    5. Writes a verification marker per stage once row counts reconcile.

Nothing here talks to Floot, Tether, or the Sales Dashboard. It only needs
outbound HTTPS to polkflpa.gov and a direct Postgres connection to Supabase.

FILL IN BEFORE RUNNING (see FEEDS below):
    - real feed URL per stage
    - real column order matching the source file AND the stage table
    - conflict_key: the column(s) that make a row unique in production
      (defaults to ["parcel_id"] — override for feeds where that's not
      unique, e.g. sales, parcel-tax, permits)

Stage tables are assumed to already exist (per your note, polk_owner_stage
already does). If a stage table doesn't exist yet, this script will fail
loudly rather than guess at DDL — see the CREATE TABLE template at the
bottom of this docstring.

    -- Template for a missing stage table (adjust types as needed):
    -- CREATE TABLE polk_<stage>_stage (LIKE polk_<stage> INCLUDING DEFAULTS);

Usage:
    export SUPABASE_DB_URL="postgresql://postgres:[email protected]:5432/postgres"

    python polk_importer.py                          # full sequence, staging mode
    python polk_importer.py --only parcel
    python polk_importer.py --resume-from sales
    python polk_importer.py --partitions 32           # more/fewer shards
    python polk_importer.py --dry-run                 # download+partition only, no DB writes
    python polk_importer.py --mode direct             # old row-by-row path (small feeds/debugging)
"""

import argparse
import csv
import hashlib
import logging
import os
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import execute_values
except ImportError:
    psycopg2 = None

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

WORKDIR = Path(os.environ.get("POLK_IMPORT_WORKDIR", "/tmp/polk_import"))
WORKDIR.mkdir(parents=True, exist_ok=True)

VERIFIED_MARKER_DIR = WORKDIR / "verified"
VERIFIED_MARKER_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_TIMEOUT = 300
DOWNLOAD_CHUNK_SIZE = 1 << 20
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5
DEFAULT_PARTITIONS = 16

SEQUENCE = ["owner", "parcel", "sales", "legal", "parcel-tax", "permits"]

