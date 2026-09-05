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
