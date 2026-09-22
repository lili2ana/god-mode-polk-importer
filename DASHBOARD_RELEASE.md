# Private inventory dashboard boundary

The deployed v2 dashboard performed five privileged inventory/lead count queries
without authenticating the request. It had no CRM or profile reader and rendered
a blanket LIVE message even when queries failed (missing counts became zero).
No cron caller was found. Database function callers for CRM are limited to the
private dispatcher. Repository search located the importer but no separate God
Mode frontend repository. These observations do not exclude callers elsewhere.

The replacement protects both HTML and JSON representations with the same private
backend authentication used by CRM/DD, under a separate `god-mode-dashboard`
scope. Five exact, head-only count queries are read-only. Errors, missing counts,
negative/non-integer values fail with 503; genuine zero remains zero. Responses
contain no contacts, owners or property records. HTML contains no scripts, secrets
or blanket pipeline-status assertion, and has no-store/CSP/referrer protections.
Lead priority is explicitly preliminary; the response authorizes neither outreach
nor acquisition. These counts are not a source-verification or underwriting gate.

The migration generates a fifth independent token inside Vault, extends the
scope constraint and private dispatch allowlist, and allows dashboard GET health
checks only through that dispatcher. It changes no schedules or existing tokens.
Existing CRM/DD handlers and business tables are unchanged.

## Browser integration boundary

Production `auth.users` contained zero accounts during the September 22 audit.
Supabase dashboard administrator sessions do not establish an application login.
Direct unauthenticated browser navigation now returns 401; this endpoint is for a
trusted backend. Do not paste a service/scoped credential into a browser, URL,
frontend environment, public repository or JavaScript bundle to bypass that gate.

A private user interface still requires an approved operator identity, a hosted
login/session path, and authorization that checks the permitted user (not merely
any authenticated Supabase user). The eventual server layer can fetch this read
model and CRM data after checking that session. Creating users, sending login
emails and a frontend/profile cutover are not part of this release. Reduced county
profiles remain offline and their production/read-cutover flags remain false.

## Verification and recovery

Local tests cover service/scoped authorization, wrong-scope denial, no database
access before authorization, count failures, valid zero, read-only query shapes,
HTML restrictions, and no automated acquisition/outreach authorization. PostgreSQL
tests exercise the new token, isolated scope, GET health routing, denial of data
dispatch and unchanged schedules. Deployment is not LIVE until source retrieval,
negative probes and an authorized production read agree with the database.

Preserve the private v2 source for audit, never as an unauthenticated rollback.
For a regression, disable this scope or deploy a corrected authenticated handler.
No production records require restoration because the handler never writes them.
