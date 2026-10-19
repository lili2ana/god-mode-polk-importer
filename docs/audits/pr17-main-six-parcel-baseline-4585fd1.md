# GIS six-parcel main-branch acceptance milestone

- Baseline merge commit: `4585fd1b231cf6781e907b369a66afe19dc98b23`
- PR: https://github.com/lili2ana/god-mode-polk-importer/pull/17 (merged)
- Verified main run (successful rerun): https://github.com/lili2ana/god-mode-polk-importer/actions/runs/39269360314
- Job: `six-parcel-gate` (successful rerun)
- Six distinct `PARCEL_PASS` assertions: 6 of 6
- Final recorded gate: `SIX_PARCEL_GATE {"passed": 6, "required": 6, "gate_pass": true, "db_writes_authorized": false}`
- Legal access: `LEGAL_ACCESS_UNVERIFIED` retained for all six parcels; Lee flood/NWI qualification incomplete.
- Charlotte ACCOUNT `402227279013`: initial lookup returned 0 records; rerun passed without changing exactly-one-record assertion. Root cause unconfirmed.

## Release interpretation
This certifies the read-only GIS acceptance suite at the baseline commit. It does **not** independently certify repository/environment `ALLOW_DB_WRITES` values or complete production seller-outreach enforcement. Production writes and outreach must remain unauthorized until separate checks and formal signoff.

## Required authorization gates
1. Verify effective `ALLOW_DB_WRITES=false` across repository, environment, job, and runtime configurations, and ensure a separate authorization gate controls any change.
2. Run isolated negative-path tests for missing/expired legal access, pending Lee flood/NWI, distress/ownership gaps, and unauthorized outreach; require zero write/outreach events.
3. For each candidate, reconcile county parcel ID/STRAP, geometry, ownership, recorded legal description, public-road frontage or recorded easement/right-of-way, recorded instrument references, title exceptions, and access continuity to a public road. Have qualified human/title review of legal sufficiency and record evidence provenance and expiry.
4. Complete flood/wetlands, zoning, buildability, financial distress and seller eligibility checks; preserve hold flags on missing/conflicting evidence.
5. Validate least-privilege service roles, idempotency, logging, alerting, rollback/kill switch, and limited nonproduction-to-production canary under explicit human signoff.
6. Authorize seller outreach separately from database writes; honor suppression/opt-outs, contact rules, and existing buyer/seller relationships.
