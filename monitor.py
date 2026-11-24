#!/usr/bin/env python3
"""God Mode CI Watch: read-only staging + GitHub change alerts.

State is restored/saved as a GitHub Actions artifact by the companion workflow.
A missing state file starts a silent baseline. Never interpret missing data as zero.
"""
import json
from contextlib import closing, contextmanager
import os
import pathlib
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "ltwqgkcblvtscgldxvjw"
REPO = "lili2ana/god-mode-polk-importer"
WORKFLOWS = ("lee-real-parcel-geometry.yml", "lee-official-source-import.yml")
JOBS = ("LEE_PARCEL_GEOMETRY", "FEMA_NFHL", "USFWS_NWI", "LEE_TAX_COLLECTOR")
TABLES = ("lee_parcel_fema_intersections", "lee_parcel_nwi_intersections",
          "lee_tax_report_import_observations", "lee_verified_owner_valuation", "lee_master_screening")
STATE_FILE = pathlib.Path(os.getenv("MONITOR_STATE_FILE", ".last_state.json"))


@contextmanager
def diagnostic_stage(stage):
    """Log only developer-controlled stage labels; never exception messages."""
    try:
        yield
    except Exception as exc:
        category = ("validation" if isinstance(exc, ValueError) else
                    "http" if isinstance(exc, urllib.error.HTTPError) else
                    "runtime")
        print(f"Monitoring blocked: stage={stage}; category={category}; snapshot not advanced.",
              file=sys.stderr)
        raise


def check_schema(cur):
    """Read-only parse and permission checks for every monitored relation."""
    required = {
        "lee_parcel_api_jobs": "source_name, job_status, total_parcels_queued, parcels_processed, block_reason, endpoint_verified",
        "lee_master_screening": "eligible_for_contact, evidence_screening_status, geometry_verified, fema_verified, nwi_verified, current_tax_verified, current_delinquency_verified, owner_value_verified, title_access_verified",
        "lee_parcel_geometry_reconciliation": "status",
        "lee_parcel_geometry_evidence": "1",
    }
    for table in TABLES:
        required.setdefault(table, "1")
    for table, columns in required.items():
        with diagnostic_stage("supabase.schema." + table):
            cur.execute(f"SELECT {columns} FROM god_mode_ops.{table} LIMIT 0")


def require(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable missing: {name}")
    return value


def safe_staging_dsn(dsn):
    """Restrict a postgres URL to the intended Supabase project, including poolers."""
    try:
        parsed = urllib.parse.urlsplit(dsn)
        host = (parsed.hostname or "").lower()
        user = urllib.parse.unquote(parsed.username or "")
        return (parsed.scheme in ("postgres", "postgresql") and
                (host == f"db.{PROJECT}.supabase.co" or
                 (host.endswith(".pooler.supabase.com") and user == f"postgres.{PROJECT}")))
    except (ValueError, TypeError):
        return False


def validate_db(db):
    jobs = db.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != set(JOBS):
        raise ValueError("Four expected source-name job records are required")
    for source, row in jobs.items():
        if not isinstance(row.get("job_status"), str) or not row["job_status"]:
            raise ValueError(f"Missing job_status for {source}")
        total, processed = row.get("total_parcels_queued"), row.get("parcels_processed")
        if any(type(v) is not int for v in (total, processed)) or total < 0 or not 0 <= processed <= total:
            raise ValueError(f"Invalid parcel counts for {source}")
    if set(db.get("table_counts", {})) != set(TABLES):
        raise ValueError("All five evidence-table counts are required")
    for name, value in db["table_counts"].items():
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid table count for {name}")
    if type(db.get("qualified")) is not int or db["qualified"] < 0:
        raise ValueError("Invalid qualified lead count")
    for field in ("geometry_evidence_count", "reconciliation_unresolved"):
        if type(db.get(field)) is not int or db[field] < 0:
            raise ValueError(f"Missing or invalid {field}")
    geometry = jobs["LEE_PARCEL_GEOMETRY"]
    db["geometry_unresolved"] = geometry["total_parcels_queued"] - geometry["parcels_processed"]
    return db


def check_supabase(dsn):
    with diagnostic_stage('supabase.staging_url'):
        if not safe_staging_dsn(dsn):
            raise ValueError('Refusing database URL: not the approved staging Supabase project')
    import psycopg2
    with closing(psycopg2.connect(dsn, connect_timeout=15,
                                  sslmode="verify-full" if os.getenv("PGSSLROOTCERT") else "require",
                                  options="-c default_transaction_read_only=on")) as conn:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            check_schema(cur)
            cur.execute("""SELECT source_name, job_status, total_parcels_queued,
                                  parcels_processed, block_reason, endpoint_verified
                           FROM god_mode_ops.lee_parcel_api_jobs""")
            rows = cur.fetchall()
            jobs = {}
            for source, status, total, processed, reason, verified in rows:
                if source not in JOBS:
                    continue
                if source in jobs:
                    raise ValueError(f"Duplicate job source: {source}")
                jobs[source] = {"job_status": status, "total_parcels_queued": total,
                                "parcels_processed": processed, "block_reason": reason,
                                "endpoint_verified": verified}
            table_counts = {}
            for table in TABLES:
                cur.execute(f"SELECT COUNT(*) FROM god_mode_ops.{table}")
                table_counts[table] = cur.fetchone()[0]
            cur.execute("""SELECT COUNT(*) FROM god_mode_ops.lee_master_screening
                WHERE eligible_for_contact IS TRUE
                  AND evidence_screening_status = 'QUALIFIED_FOR_CONTACT_ENRICHMENT'
                  AND geometry_verified AND fema_verified AND nwi_verified
                  AND current_tax_verified AND current_delinquency_verified
                  AND owner_value_verified AND title_access_verified""")
            qualified = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM god_mode_ops.lee_parcel_geometry_evidence")
            geometry_evidence = cur.fetchone()[0]
            cur.execute("""SELECT COUNT(*) FROM god_mode_ops.lee_parcel_geometry_reconciliation
                           WHERE status='GEOMETRY_UNRESOLVED'""")
            unresolved = cur.fetchone()[0]
    with diagnostic_stage("supabase.validate_counts"):
        return validate_db({"jobs": jobs, "table_counts": table_counts, "qualified": qualified,
                            "geometry_evidence_count": geometry_evidence,
                            "reconciliation_unresolved": unresolved})


def get_json(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "God-Mode-CI-Watch"})
    with urllib.request.urlopen(req, timeout=25) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("Unexpected GitHub JSON response")
    return data


def fallback_repository_runs(root, token):
    """Locate PR-branch workflows not yet registered on the default branch."""
    found = {}
    for page in range(1, 11):
        data = get_json(root + f"/actions/runs?per_page=100&page={page}", token)
        runs = data.get("workflow_runs")
        if not isinstance(runs, list) or type(data.get("total_count")) is not int:
            raise ValueError("Repository workflow history unavailable")
        for run in runs:
            path = str(run.get("path") or "").split("@", 1)[0]
            for wf in WORKFLOWS:
                if path == f".github/workflows/{wf}" and wf not in found:
                    found[wf] = run
        if len(found) == len(WORKFLOWS) or page * 100 >= data["total_count"]:
            return found
    raise ValueError("GitHub run history too deep to verify missing workflow")


def check_github(token):
    root = f"https://api.github.com/repos/{REPO}"
    pr = get_json(root + "/pulls/22", token)
    if type(pr.get("merged")) is not bool:
        raise ValueError("PR merge state missing")
    workflows = {}
    fallback = None
    for name in WORKFLOWS:
        url = root + "/actions/workflows/" + name + "/runs?per_page=1"
        try:
            data = get_json(url, token)
            runs = data.get("workflow_runs")
            if not isinstance(runs, list):
                raise ValueError(f"Workflow history unavailable: {name}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            if fallback is None:
                fallback = fallback_repository_runs(root, token)
            runs = [fallback[name]] if name in fallback else []
        if not runs:
            workflows[name] = {"run_id": None, "run_attempt": None, "status": "no_runs",
                               "conclusion": None, "html_url": None}
            continue
        run = runs[0]
        if (type(run.get("id")) is not int or type(run.get("run_attempt", 1)) is not int
                or not isinstance(run.get("status"), str)
                or not isinstance(run.get("html_url"), str)):
            raise ValueError(f"Invalid workflow run data: {name}")
        workflows[name] = {"run_id": run["id"], "run_attempt": run.get("run_attempt", 1),
                           "status": run["status"], "conclusion": run.get("conclusion"),
                           "html_url": run["html_url"]}
    return {"pr": {"merged": pr["merged"], "html_url": pr.get("html_url", root + "/pulls/22")},
            "workflows": workflows}


def diff(previous, current):
    if previous is None:
        return []
    alerts = []
    old_db, new_db = validate_db(previous["db"]), validate_db(current["db"])
    old_jobs, new_jobs = old_db["jobs"], new_db["jobs"]
    old_geo, new_geo = old_jobs["LEE_PARCEL_GEOMETRY"], new_jobs["LEE_PARCEL_GEOMETRY"]
    a, b = old_geo["parcels_processed"], new_geo["parcels_processed"]
    if (a != b or old_db["geometry_unresolved"] != new_db["geometry_unresolved"]
            or old_db["reconciliation_unresolved"] != new_db["reconciliation_unresolved"]
            or old_db["geometry_evidence_count"] != new_db["geometry_evidence_count"]):
        alerts.append(f"Geometry: {a:,} → {b:,} processed; "
                      f"{old_db['geometry_unresolved']:,} → {new_db['geometry_unresolved']:,} unresolved "
                      f"(queued {new_geo['total_parcels_queued']:,}); "
                      f"county polygons {old_db['geometry_evidence_count']:,} → {new_db['geometry_evidence_count']:,}; "
                      f"unresolved reconciliation rows {old_db['reconciliation_unresolved']:,} → {new_db['reconciliation_unresolved']:,}.")
    for job in JOBS:
        old, new = old_jobs[job], new_jobs[job]
        if old["job_status"] != new["job_status"]:
            alerts.append(f"Job {job}: {old['job_status']} → {new['job_status']} (verify provenance).")
        if old.get("block_reason") != new.get("block_reason"):
            alerts.append(f"Job {job}: source/block reason changed; inspect staging job record.")
        if old.get("endpoint_verified") != new.get("endpoint_verified"):
            alerts.append(f"Job {job}: endpoint_verified {old.get('endpoint_verified')} → {new.get('endpoint_verified')}.")
        if job != "LEE_PARCEL_GEOMETRY" and old["parcels_processed"] != new["parcels_processed"]:
            alerts.append(f"Job {job}: processed {old['parcels_processed']:,} → {new['parcels_processed']:,} (job-reported).")
    for table in TABLES:
        x, y = old_db["table_counts"][table], new_db["table_counts"][table]
        if x != y:
            alerts.append(f"Staging {table}: {x:,} → {y:,} rows (source validation still required).")
    if new_db["qualified"] > old_db["qualified"]:
        alerts.append(f"Verified qualified leads: {old_db['qualified']:,} → {new_db['qualified']:,}.")
    if not previous["github"]["pr"]["merged"] and current["github"]["pr"]["merged"]:
        alerts.append(f"PR #22 merged: {current['github']['pr']['html_url']}")
    for wf in WORKFLOWS:
        x = previous["github"]["workflows"][wf]
        y = current["github"]["workflows"][wf]
        fresh = (x.get("run_id"), x.get("run_attempt")) != (y.get("run_id"), y.get("run_attempt"))
        finished_now = x.get("conclusion") != y.get("conclusion")
        if y.get("status") == "completed" and y.get("conclusion") in ("success", "failure") and (fresh or finished_now):
            word = "succeeded" if y["conclusion"] == "success" else "failed"
            alerts.append(f"Workflow {wf} {word} (run {y['run_id']}, attempt {y['run_attempt']}): {y['html_url']}. "
                          "CI result only; not evidence of imported records.")
    return alerts


def webhook_payload(kind, message):
    if kind == "discord":
        return {"content": message}
    if kind == "slack":
        return {"text": message}
    raise ValueError("ALERT_WEBHOOK_KIND must be discord or slack")


def webhook_kind(url):
    explicit = os.getenv("ALERT_WEBHOOK_KIND", "").strip().lower()
    if explicit:
        if explicit not in ("discord", "slack"):
            raise ValueError("Unsupported ALERT_WEBHOOK_KIND")
        return explicit
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in ("discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    raise ValueError("Set ALERT_WEBHOOK_KIND explicitly (discord or slack)")


def send_webhook(url, kind, message):
    data = json.dumps(webhook_payload(kind, message)).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "God-Mode-CI-Watch"})
    with urllib.request.urlopen(req, timeout=25) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError("Webhook delivery returned non-success status")


def load_previous_state():
    if not STATE_FILE.exists():
        return None
    with STATE_FILE.open(encoding="utf-8") as stream:
        state = json.load(stream)
    if state.get("version") != 1:
        raise ValueError("Unsupported or corrupt previous monitoring snapshot")
    validate_db(state["db"])
    if set(state["github"]["workflows"]) != set(WORKFLOWS):
        raise ValueError("Incomplete previous workflow snapshot")
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="monitor-state-", dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(name, STATE_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    with diagnostic_stage("config.staging_dsn"):
        dsn = require("STAGING_SUPABASE_DB_URL")
    with diagnostic_stage("config.github_token"):
        token = require("GH_MONITOR_TOKEN")
    with diagnostic_stage("config.webhook_url"):
        url = require("ALERT_WEBHOOK_URL")
    with diagnostic_stage("config.webhook_kind"):
        kind = webhook_kind(url)
    with diagnostic_stage("snapshot.restore"):
        previous = load_previous_state()
    with diagnostic_stage("supabase.read"):
        db = check_supabase(dsn)
    with diagnostic_stage("github.read"):
        github = check_github(token)
    current = {"version": 1, "db": db, "github": github}
    with diagnostic_stage("snapshot.compare"):
        alerts = diff(previous, current)
    if alerts:
        with diagnostic_stage("webhook.delivery"):
            send_webhook(url, kind, "God Mode CI Watch\n" + "\n".join(alerts))
        print(f"Delivered {len(alerts)} meaningful change notification(s).")
    else:
        print("Baseline captured or no meaningful changes; notification suppressed.")
    with diagnostic_stage('snapshot.save'):
        save_state(current)
    print("Read-only staging/GitHub check finished; snapshot saved.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Monitoring blocked; snapshot not advanced.", file=sys.stderr)
        sys.exit(1)
