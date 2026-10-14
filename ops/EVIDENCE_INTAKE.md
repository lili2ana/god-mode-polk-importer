# County evidence intake

The database schema/rules and Osceola network probe are deployed. Document and
ATTOM acquisition adapters and the manual workflow are committed code, not a
credentialed live feed. No paid account was enrolled and no seller outreach sent.

## Credentials and execution

Use GitHub repository secrets or the operator host's secret environment, never
committed manifests or chat:

| Configuration | Purpose |
| --- | --- |
| `LEE_FTP_HOST`, `LEE_FTP_USER`, `LEE_FTP_PASSWORD` | Authorized Clerk account. FTPS handshake and encrypted data channel are required; Clerk must supply the host and exact index/file paths. |
| `LEE_FTP_PORT` | Defaults to 21; explicit TLS. |
| `ATTOM_API_KEY` | ATTOM key with relevant foreclosure/history entitlement. |
| `GOD_MODE_DATABASE_URL` | Local operator database credential. GitHub workflow reuses `SUPABASE_DB_URL`. |
| `PGSSLROOTCERT` | Root certificate file if the server CA is not trusted by the system store. TLS verify-full stays enabled. |
| `DOCUMENT_INTAKE_TOKEN` | >=32 random characters, local intake authentication only. |
| `DOCUMENT_PROVIDER_HOST`, `DOCUMENT_API_KEY` | Optional authorized aggregator HTTPS endpoint and bearer key; download URL comes from its documented record response. No cross-host redirects. |

Install `ops/requirements-evidence.txt` plus poppler-utils and tesseract-ocr.
The default scratch inbox is temporary. On an operator host set `--inbox` to a
durable private directory with backups. The local endpoint accepts raw file
bytes at POST `/documents` with Authorization Bearer and `X-Parcel-Id`,
`X-Instrument-Reference`, `X-Source-Url`. Bind is 127.0.0.1 only; use an approved
private tunnel/reverse proxy for remote access. A staged response is not legal
approval. If database credentials are absent it returns `database_staged:false`
and preserves the extracted local evidence for later intake.

```
python ops/evidence_pipeline.py serve --port 8765 --inbox /private/document-inbox
python ops/evidence_pipeline.py manifest --manifest /private/clerk-manifest.json --inbox /private/document-inbox
python ops/evidence_pipeline.py attom --parcel 334426L3030160020 --county Lee --output /private/distress.json
python ops/evidence_pipeline.py queue
```

Manifest array entries require parcel_id, instrument_reference, source_url and
remote_path (Clerk FTPS) or download_url (configured aggregator). The Lehigh record
needed is PB 15 PG 100, Unit 3, Block 16, Lot 2 plus later access/restriction
instruments. The exact Clerk filename/path is unknown; don't invent a download
path. Up to 25 documents per batch, <=50MB each, <=100 pages; image conversion
has a 100-million-pixel budget. Original bytes and processing PDF hashes are
separate. The archive retains source bytes and page-indexed evidence candidates.

GitHub `County document and distress intake` is workflow_dispatch only and needs
the workflow on the repository's default branch before normal dispatch. The
optional manifest must be committed and contain no secrets or signed URLs.
Documents stay private on the runner; current workflow does not upload artifacts
to public GitHub. Runner storage is ephemeral: database stores extraction evidence,
not file bytes. For durable source retention use the private operator inbox until
a private object-store archive is configured. No S3 archive was deployed.

## Distress contract

`evidence_contract.schema.json` defines canonical records. The SQL function
`distress_evidence_qualifies` enforces exact parcel/county/current-owner binding,
HTTPS source, record ID/hash, current unresolved activity, event date <=today,
and adverse/release searches within seven days. An unpaid tax certificate must
have amount_due >0 and delinquent_since strictly before today minus 12 months.
Only lis_pendens, notice_of_default, tax_certificate_delinquency qualify under
this contract. Absentee occupancy alone is not qualifying distress.

ATTOM `/saleshistory/expandedhistory` candidates bind APN and county FIPS. Historical
foreclosure entries do not establish that a filing is still active. No records
returned is not a negative-search certificate. Candidate intake always writes
REVIEW_REQUIRED, even if an incoming payload claims VERIFIED. Verification needs
the official instrument and a current release/resolution check; no fabricated
owner, certificate date, release result or document approval is supplied.

Polk's existing seller eligibility function remains in place. Lee/Charlotte/
other non-Polk county qualification now uses the canonical adverse-record gate;
legacy occupancy/tax flags cannot substitute for this required proof.

## Osceola network evidence

Official OsceolaGIS item 8c5a166d7aee4d5ca1cb9179313e2f48 provides hosted roads.
St. Cloud maintained-road item 4f31c0f22e09439bbd27f74f7ac531c6 covers only the
St. Cloud JPA, including privately maintained roads, with an older published
snapshot. Queries use the actual parcel polygon plus a 30m network search.
Features are retained as candidates. Neither road names, speed limits, private
ownership, old maintenance snapshots nor network proximity prove pavement or
recorded public ROW; those statuses remain REVIEW_REQUIRED. The Alaska DOT
Road_Surface layer and 2025 parcels in a consultant's AGOL organization were
excluded from current official Osceola evidence.

## Latest live queue result

October 12, 2026, 17:15 EDT: one active Lehigh hold evaluated, zero newly qualified
leads, no active Port Charlotte holds. 1102 FLOYD AVE S is held for recorded
frontage/access and seller distress, with rough comps passing. 4239 CONWAY BLVD
and three prior Lehigh flood failures remain rejected and were not reopened.

Apply distress_evidence.sql after county_preflight.sql, and county_sources.sql
then osceola_road_network.sql before the updated county GIS worker. Reapplying
older preflight alone restores its older non-Polk distress function; use the full
ordered deployment.

GitHub optionally accepts SUPABASE_DB_CA_CERT exported from database settings;
the workflow writes it to a temporary file and sets PGSSLROOTCERT without
logging certificate or DSN contents. Remote GitHub DB connection remains untested.
