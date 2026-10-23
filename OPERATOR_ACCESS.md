# Operator read gateway — local operator access verified

September 26 update (supersedes preparation status below): the approved operator
completed provider identity verification and explicitly authorized read-only access.
The service-only active-session migration and gateway v1 are deployed. Actual
browser login, dashboard counts, capped CRM reads and logout succeeded. Independent
SQL matched all five counts and found no remaining session after logout; replaying
that session's JWT against the gateway returned 401. Anonymous, public-key and
forged-token reads returned 401. Account identifiers and evidence remain private.

This verifies the local, single-process workspace with hosted Auth and gateway;
it is not a public hosted frontend release. Natural refresh/expiry, a second
unapproved real identity, account removal and operator-disable checks remain open.
The historical preparation notes below describe the design and remaining gates.

Native form documents now use same-origin referrer policy while callbacks and
redirects retain no-referrer. A rejected workspace gate also attempts local-scope
Auth logout of newly issued tokens, including upstream failures and cancellation;
failed cleanup is not reported as confirmed logout.

`god-mode-operator-read` provides the server authorization boundary for a future
private dashboard/CRM interface. It accepts a user's Bearer access token, calls
Supabase Auth `getUser(token)` on every request, and requires the returned UUID
to appear in the server-only `GOD_MODE_OPERATOR_USER_IDS` allowlist. A confirmed
email and an active Auth session belonging to that UUID are also required. Missing or
malformed configuration fails closed. Anonymous users, public/machine credentials,
unapproved users and editable metadata cannot grant access. No operator is seeded.

After authorization it can GET only the existing dashboard JSON counts or the
capped CRM feed. Caller-controlled destinations, DD jobs, writes, cookies and
credentials are not forwarded. Server credentials remain on the fixed private
service connection; redirects are rejected. Responses are JSON/no-store, without
upstream headers, cookies or wildcard CORS. Auth and read calls have bounded
timeouts. Source failures produce generic errors without logging tokens or data.

This function is a backend bearer-token adapter. The separately runnable
[operator web interface](operator-web/README.md) now supplies a local sign-in,
refresh, sign-out and dashboard/CRM UI, with Auth tokens held on the server.
It is not deployed or verified with production login. The gateway has no account-creation or
email-sending behavior. `getUser` is used for a fresh server-confirmed user record,
not `getSession` or unchecked client claims; see the
[Supabase authentication guidance](https://supabase.com/docs/guides/getting-started/tutorials/with-nextjs).

## Gates before production

1. The operator email has been selected and recorded privately; do not put it in
   source code or assume mailbox ownership from that selection. Bind the approved account
   to its actual Auth UUID; do not authorize an email or user-editable metadata.
2. Verify the implemented local login/refresh/logout routes with actual Auth.
   For email codes, configure the sender/template before enabling delivery.
   GitHub PKCE is the selected setup route after Microsoft directory access was
   denied. Both provider flows are tested locally and disabled by default;
   GitHub application/provider configuration and verified identity enrollment
   remain unproved. See the web README for the concrete configuration and gates.
   No CORS is configured here: use a same-origin server integration, or separately
   implement and test an explicit origin policy. Do not put machine credentials
   into browser code to work around this boundary.
3. Verify real valid/expired/revoked tokens, a second unapproved user, sign-out,
   account removal, and operator disable against the actual Auth service. Local
   tests use a fake Auth client and are not production JWT/session evidence.
   An active-session check is now implemented and tested with fixtures, but real
   hosted Auth login/logout behavior must still be demonstrated.
4. Configure the allowlist only after account verification and prove authorized
   dashboard/CRM reads plus negative probes. Review frontend token storage and
   token-free server/access logging before enabling operator access.

The existing five service endpoints and daily jobs are unchanged. No new function,
environment variable, user, database grant, login email or frontend has been
deployed by this preparation. No profile cutover or seller outreach is enabled.

## Active-session boundary (READY, not deployed)

The separate local `operator-web/enroll-server.ts` now supplies an enrollment-only
observation route without requiring a pre-existing approved UUID. It grants no
workspace access and writes no allowlist. Its confirmed Auth UUID observation must
be reconciled with the intended operator's server-controlled provider identity
before approval; see the web README. This preparation has not created an account,
enabled a provider or verified a hosted login.

After `getUser` verifies the exact supplied token, the handler decodes its claims
and validates issuer, audience, role, expiry, subject and session UUID. Decoding
never substitutes for Auth signature verification. A service-only boolean RPC
then checks `auth.sessions` against the confirmed user's UUID on every read.
The actual hosted column names were inspected read-only before implementation.

The session must exist and have no elapsed `not_after`; its user must be email
confirmed, non-anonymous, not deleted and not currently banned. Missing sessions,
lookup errors and malformed results deny access before any dashboard/CRM request.
No session result is cached. This follows the documented
[Supabase logout/session guidance](https://supabase.com/docs/guides/auth/sessions).
It does not promise cancellation of an already-authorized request or enforce
additional inactivity/single-session rules beyond the checked database state.

The migration creates a fixed `SECURITY DEFINER` lookup with an empty search path.
Only `service_role` can call it; PUBLIC, anon and authenticated cannot. It exposes
no Auth records and grants no direct table access. Apply the migration before the
handler; without it the handler fails closed. Rollback disables the new operator
route or clears its allowlist, rather than restoring a session-blind reader.
Do not change the five existing private service endpoints or daily jobs.

Local validation: all 95 Deno tests passed, including 16 operator, 13 web-session
and 24 OAuth cases across Microsoft and GitHub. The actual SDK uses fake HTTP;
the full runtime dependency graph is locked and CI tests enforce the frozen lock.
Four new disposable PostgreSQL tests cover service-only execution, cross-user
sessions, removal, expiry and user-state checks. They require CI's PostgreSQL 17
fixture; fixture success does not prove hosted login/logout or browser behavior.
CI discovers all function test files rather than only `security_test.ts`.
