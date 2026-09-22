# CRM and due-diligence security release — September 22, 2026

The CRM feed and two daily DD functions now authorize requests before business
queries. Anonymous requests, public keys, forged credentials and cross-worker
credentials cannot retrieve contacts or start DD. This is a backend integration;
it does not supply a browser login or authorize every signed-in user.

## Authentication and scheduling

- Handlers accept an exact server default secret key through `apikey`, the exact
  configured legacy service credential through Bearer, or a scope-specific
  256-bit internal token through `x-god-mode-token`. Public keys/user JWTs are not
  authorization. Gateway `verify_jwt=false` is intentional: the handler validates
  credentials, including opaque internal tokens, itself.
- The three scoped credentials are generated inside PostgreSQL and encrypted in
  Vault. Only hashes are stored in the private credential registry. The public
  digest-check RPC is executable only by `service_role`, has a fixed empty search
  path, and can return only a boolean; it cannot retrieve tokens or arbitrary data.
- The private, invoker-only dispatcher has fixed function destinations, restricted
  methods, a maximum batch size of five and an advisory lock. It reads Vault at
  invocation time, never embeds credentials in cron command text, and cannot fetch
  CRM contacts (CRM health checks only). No credential is committed to this repo.
- Schedules remain **06:05 GMT** for DD and **06:15 GMT** for finalization. The
  dispatcher now uses synchronous HTTP and stores only sanitized HTTP/processing
  results in `god_mode_ops.internal_function_requests`. Inspect `result.ok` and
  processing errors; cron SQL success alone is not a processing-success guarantee.
- Functions have a 25-second GIS budget and 10-second per-request maximum. The
  dispatcher has a 45-second HTTP maximum and restores its previous timeout.
  Timed-out invocations may have started remote work: inspect audit/data before
  retrying. Database rollback cannot undo an already executed remote HTTP action.

### Why the dispatcher avoids pg_net

Production inspection found PUBLIC grants on the extension-owned HTTP queue and
response tables. The intermediate REVOKE migration could not alter grants owned
by `supabase_admin`; a successful migration response did not establish effective
revocation. The final dispatcher therefore never queues a credential or result in
pg_net. Initial credentials used for health probes were rotated in Vault. Existing
extension ACLs are not represented as fixed. The ineffective attempt remains in
migration history and is explicitly superseded by the synchronous dispatcher.

## Evidence handling

Unknown/blank tax amounts remain unknown; zero requires an actual numeric zero
from the current source. GIS HTTP errors, ArcGIS error objects, malformed/truncated
responses and unresolved parcel identity are rejected. Optional source failures
persist explicit unknown/review evidence without dropping the entire property.
Point-only flood/wetlands screens never certify a whole parcel as clear.

Unqualified nearby sale samples are exploratory evidence only. They no longer
overwrite property market value or produce a MAO/confidence score. The finalizer
uses only the stored `assessed_value` as a labeled reference, never relabels
`estimated_market_value` as an assessor value. Both keep underwriting in review,
with MAO/value/confidence null and automated-acquisition eligibility false.
Property write failures and required-source failures are recorded rather than
silently counted as completed work.

All **50** existing machine reviews were already `review_required`. Their full
original rows are preserved in private `god_mode_ops.dd_security_backup`; their
unsupported DD scores were set to zero and underwriting replaced by an explicit
review gate. Human-approved and already-safe reviews are excluded. The migration
aborts if the legacy population unexpectedly exceeds 100. This does not retroactively
verify old GIS findings or existing property values.

## Executed production verification

- All three endpoints rejected anonymous, forged scoped and invalid public-key
  probes with **401** (nine probes). CRM also rejected a valid DD token with 401.
- Each endpoint returned **200** for its own scoped health check via the final
  synchronous path, with no business side effects (requests 1000000–1000002).
- An authorized CRM read returned **200**, `ok=true`, and its capped **200-contact**
  page. Only status/count were inspected in output; no outreach was performed.
- Finalizer request **1000004** and worker request **1000005** each returned 200,
  `ok=true`, one processed property and zero processing errors. Resulting reviews
  remained gated. The worker's comp source was unavailable and explicitly flagged;
  no MAO or market-value estimate was promoted.
- A prior worker probe (1000003) surfaced a source-processing failure. Optional
  source isolation was fixed and regression-tested before the successful repeat.
- Deployed versions: CRM **3**, DD worker **6**, finalizer **4**.
- The first secured DD worker cron succeeded at **06:05:00-06:05:23 UTC** on
  September 22. Request **1000006** returned HTTP 200, `ok=true`, five processed
  properties and zero errors. The 06:15 finalizer is still pending at this update.

## Title/access follow-up

Read-only source inspection found the deployed title/access worker had no handler
authentication and gateway verification disabled. It could update review findings
without authorization. No cron or database function caller was found; the latest
recorded invocation was September 4. No anonymous write probe was performed.

The follow-up handler uses the shared private authentication gate and an independent
Vault credential. It accepts POST only, caps work at five reviews, and times out
source reachability probes after eight seconds. It preserves underwriting and legal
statuses, excludes non-review records, and compares `updated_at` before replacing
findings so a concurrent edit becomes a reported conflict. Stored road signals are
not promoted to verified road proximity. Endpoint reachability never clears title.
Write/conflict/audit failures return `ok=false`; no contact information is returned.

The migration extends the fixed private dispatcher without changing daily schedules
or rotating the existing CRM/DD credentials. Title/access remains manually invoked.
At code preparation this follow-up is READY, pending CI and production verification.
Rollback means deploying a corrected authenticated handler or disabling its scoped
credential; never redeploy the unauthenticated original.

Tests cover actual handler behavior, credential isolation, denied requests without
business queries, safe health checks, unknown-vs-zero evidence, GIS failures,
write failures, deadlines, PostgreSQL permissions, fixed destinations, credential
rotation and reversible quarantine. No real credentials or source property data are
included in fixtures. Existing private tables intentionally have RLS without public
policies (default deny), as described by the [Supabase advisor](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).

## Remaining gates and rollback

The authenticated backend is working; verified comps, full cost underwriting,
title/access review and a private user-facing profile integration remain unfinished.
Seller outreach and countywide cleanup remain off. Other deployed functions are
outside these reviewed endpoints and require their own access audit, including the
aggregate dashboard. Private browser login and dashboard read integration remain
separate unfinished work.

For a regression, disable the affected cron job and scoped credential, preserve the
private request log, and deploy a corrected authenticated handler. Never restore
the unauthenticated original endpoint or direct credential-bearing pg_net calls.
Original DD rows are retained for deliberate, reviewed recovery, not automatic
restoration of unsupported valuations. Revocation/rotation must update Vault and
the matching digest together in a transaction; scoped token values never belong
in client code, repository files or logs.
