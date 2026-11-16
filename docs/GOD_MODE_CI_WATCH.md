# God Mode CI Watch

Read-only, scheduled staging and GitHub monitoring for PR #22.

## Installation
The following files are committed on the PR #22 feature branch:
- `monitor.py`
- `tests/test_monitor.py`
- `.github/workflows/god-mode-ci-watch.yml`

## GitHub Actions configuration
Repository Settings > Secrets and variables > Actions:
- Secret `STAGING_SUPABASE_DB_URL`: Postgres connection URL **only** for staging project `ltwqgkcblvtscgldxvjw`.
- Secret `ALERT_WEBHOOK_URL`: Slack or Discord incoming webhook URL.
- Optional secret `SUPABASE_DB_CA_CERT`: database CA certificate text.
- Optional repository variable `ALERT_WEBHOOK_KIND`: `slack` or `discord`. If absent, standard Slack and Discord hosts are recognized.
- `GH_MONITOR_TOKEN` uses the workflow's built-in `github.token`, so no personal access token is required.

Workflow permissions: `contents: read`, `actions: read`. Do not give the monitor a production DSN.

## Operation
GitHub scheduled workflows only run on the default branch. Once this workflow is merged into `main`, go to Actions > God Mode CI Watch > Run workflow to establish a silent baseline. The scheduled job runs every 30 minutes. A state snapshot is stored as a 7-day GitHub Actions artifact. If the artifact expires, the next run silently establishes a new baseline.

The monitor reports:
- Lee parcel geometry progress: `LEE_PARCEL_GEOMETRY.parcels_processed` and `total_parcels_queued - parcels_processed`.
- Source-backed polygon count and the independent `GEOMETRY_UNRESOLVED` reconciliation count.
- All four source job statuses and block reasons.
- FEMA, NWI, tax, owner valuation and master screening evidence counts.
- PR #22 merge status.
- New successful or failed runs of `lee-real-parcel-geometry.yml` and `lee-official-source-import.yml`.

Baseline verified November 16, 2026: 2,710 geometry records, 666 unresolved, 3,376 queued, zero qualified leads. These are **not** hardcoded in the monitor.

## Testing
```bash
python -m pip install 'psycopg2-binary>=2.9,<3'
python -m unittest discover -s tests -p 'test_monitor.py' -v
```

## Safeguards
Database access uses read-only transactions and validates the staging project identity. GitHub 403 and unexpected errors fail closed. The first run never alerts for historical events. A webhook delivery error prevents advancing the state. A workflow success is not proof that official data was imported or that leads are qualified.

For a PR #22 merge alert, the monitoring workflow must already be running on the default branch before that PR is merged. Otherwise its first run establishes a silent baseline.
