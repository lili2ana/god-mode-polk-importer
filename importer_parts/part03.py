)
log = logging.getLogger("polk_importer")


# --------------------------------------------------------------------------
# Download + decompress
# --------------------------------------------------------------------------

def download_with_retries(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        log.info("Already downloaded, skipping: %s", dest.name)
        return dest

    tmp_dest = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("Downloading (attempt %d/%d): %s", attempt, MAX_RETRIES, url)
            if url.startswith("ftps://"):
                from ftplib import FTP_TLS
                from urllib.parse import urlparse
                parsed = urlparse(url)
                ftp = FTP_TLS(timeout=DOWNLOAD_TIMEOUT)
                ftp.connect(parsed.hostname, parsed.port or 21)
                ftp.login("anonymous", "anonymous@")
                ftp.prot_p()
                remote_path = parsed.path
                written = 0
                with open(tmp_dest, "wb") as f:
                    def _write(chunk):
                        nonlocal written
                        f.write(chunk)
                        written += len(chunk)
                    ftp.retrbinary(f"RETR {remote_path}", _write, blocksize=DOWNLOAD_CHUNK_SIZE)
                ftp.quit()
                if written == 0:
                    raise IOError("FTPS download returned 0 bytes")
            else:
                with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    written = 0
                    with open(tmp_dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                written += len(chunk)
                    if total and written != total:
                        raise IOError(f"Incomplete download: got {written} of {total} bytes")
            tmp_dest.rename(dest)
            log.info("Downloaded %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
            return dest
        except Exception as e:
            log.warning("Download failed (attempt %d): %s", attempt, e)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError("unreachable")


def extract_zip(zip_path: Path, extract_dir: Path) -> list[Path]:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        log.info("Extracting %d file(s) from %s", len(names), zip_path.name)
        zf.extractall(extract_dir)
    return [extract_dir / n for n in names]


# --------------------------------------------------------------------------
# Row parsing (source file -> normalized tuples)
# --------------------------------------------------------------------------

def iter_source_rows(stage: FeedStage, data_file: Path):
    """Yield one normalized row (tuple) at a time, in stage.columns order."""
    if stage.is_fixed_width:
        yield from _iter_fixed_width_rows(stage, data_file)
    elif stage.name == "legal":
        yield from _iter_legal_rows(stage, data_file)
    else:
        yield from _iter_delimited_rows(stage, data_file)


def _iter_delimited_rows(stage: FeedStage, data_file: Path):
    with open(data_file, "r", encoding=stage.encoding, newline="") as f:
        reader = csv.reader(f, delimiter=stage.delimiter)
        if stage.has_header:
            next(reader, None)
        for row in reader:
            if not row:
                continue
            row = (row + [""] * len(stage.columns))[: len(stage.columns)]
            yield tuple(row)


def _strip_outer_csv_quotes(value: str) -> str:
    value = value.rstrip("\r\n")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value.strip('"')


def _parse_legal_record_start(line: str):
    """Return (structural_fields, dscr_tail) when a physical line begins a legal record.

    Polk's first seven fields are coded numeric identifiers. DSCR is free text and may
    contain commas, quote marks, and embedded physical newlines. A physical line that
    does not begin with seven valid numeric structural fields is therefore treated as a
    continuation of the prior DSCR instead of an immediate fatal parse error.
    """
    parts = line.rstrip("\r\n").split(",", 7)
    if len(parts) != 8:
        return None
    structural = [_strip_outer_csv_quotes(v) for v in parts[:7]]
    if not all(v.isdigit() for v in structural):
        return None
    return structural, _strip_outer_csv_quotes(parts[7])


def _iter_legal_rows(stage: FeedStage, data_file: Path):
    """Parse Polk legal rows using the verified seven-field structural boundary.

    The first seven source fields are coded numeric identifiers and define the start
    of a logical record. DSCR is allowed to span multiple physical lines. Continuation
    lines are appended to the pending DSCR. Lines that cannot be attached to a logical
    record are quarantined and logged instead of aborting the entire feed.
    """
    expected = len(stage.columns)
    if expected != 8:
        raise ValueError(f"legal parser expects 8 configured columns, got {expected}")

    quarantine_path = WORKDIR / "legal_quarantine.txt"
    quarantine_count = 0
    continuation_count = 0
    pending_structural = None
    pending_dscr_parts = []
    pending_start_line = None

    def flush_pending():
        if pending_structural is None:
            return None
        dscr = " ".join(p for p in pending_dscr_parts if p).strip()
        return tuple(list(pending_structural) + [dscr])

    with open(data_file, "r", encoding=stage.encoding, newline="") as f, \
         open(quarantine_path, "w", encoding="utf-8", newline="") as quarantine:
        if stage.has_header:
            header_line = f.readline()
            header_parts = header_line.rstrip("\r\n").split(",", 7)
            header = tuple(_strip_outer_csv_quotes(v) for v in header_parts)
            expected_header = tuple(c.upper() for c in stage.columns)
            if header != expected_header:
                raise ValueError(f"legal header mismatch: {header!r} != {expected_header!r}")

        for physical_line_no, line in enumerate(f, start=2 if stage.has_header else 1):
            if not line.strip():
                continue

            parsed = _parse_legal_record_start(line)
            if parsed is not None:
                row = flush_pending()
                if row is not None:
                    if len(row) != 8:
                        raise AssertionError(f"legal normalized width != 8 near physical line {pending_start_line}")
                    yield row
                pending_structural, dscr = parsed
                pending_dscr_parts = [dscr]
                pending_start_line = physical_line_no
                continue

            continuation = _strip_outer_csv_quotes(line).strip()
            if pending_structural is not None:
                if continuation:
                    pending_dscr_parts.append(continuation)
                    continuation_count += 1
                continue

            quarantine.write(f"{physical_line_no}\t{line.rstrip()}\n")
            quarantine_count += 1

        row = flush_pending()
        if row is not None:
            if len(row) != 8:
                raise AssertionError(f"legal normalized width != 8 near physical line {pending_start_line}")
            yield row

    log.info(
        "Legal parser recovery summary: continuation_lines=%d quarantined_unattached_lines=%d quarantine=%s",
        continuation_count,
        quarantine_count,
        quarantine_path,
    )


def _iter_fixed_width_rows(stage: FeedStage, data_file: Path):
    if not stage.fixed_width_spec:
        raise ValueError(f"{stage.name}: fixed_width_spec required when is_fixed_width=True")
    with open(data_file, "r", encoding=stage.encoding) as f:
        for line in f:
            yield tuple(line[start:end].strip() for start, end in stage.fixed_width_spec)


# --------------------------------------------------------------------------
# Parcel-prefix partitioning
# --------------------------------------------------------------------------

def partition_for(parcel_id: str, num_partitions: int) -> int:
    """Stable hash-based bucket — avoids the skew you'd get bucketing on
    literal first-character prefix (parcel IDs aren't evenly distributed
    alphabetically/numerically)."""
    digest = hashlib.md5(parcel_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % num_partitions
