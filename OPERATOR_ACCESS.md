# Operator read gateway — READY, not deployed

`god-mode-operator-read` provides the server authorization boundary for a future
private dashboard/CRM interface. It accepts a user's Bearer access token, calls
Supabase Auth `getUser(token)` on every request, and requires the returned UUID
to appear in the server-only `GOD_MODE_OPERATOR_USER_IDS` allowlist. Missing or
malformed configuration fails closed. Anonymous users, public/machine credentials,
unapproved users and editable metadata cannot grant access. No operator is seeded.

After authorization it can GET only the existing dashboard JSON counts or the
capped CRM feed. Caller-controlled destinations, DD jobs, writes, cookies and
credentials are not forwarded. Server credentials remain on the fixed private
service connection; redirects are rejected. Responses are JSON/no-store, without
upstream headers, cookies or wildcard CORS. Auth and read calls have bounded
timeouts. Source failures produce generic errors without logging tokens or data.

This is a backend bearer-token adapter, not a login page, session issuer, cookie
store, token refresher or production access grant. It has no account-creation or
email-sending behavior. `getUser` is used for a fresh server-confirmed user record,
not `getSession` or unchecked client claims; see the
[Supabase authentication guidance](https://supabase.com/docs/guides/getting-started/tutorials/with-nextjs).

## Gates before production

1. Resolve the already-requested operator email without assuming identity from
   a GitHub or Supabase dashboard administrator session. Bind the approved account
   to its actual Auth UUID; do not authorize an email or user-editable metadata.
2. Implement and verify the chosen login/refresh/logout route and frontend host.
   No CORS is configured here: use a same-origin server integration, or separately
   implement and test an explicit origin policy. Do not put machine credentials
   into browser code to work around this boundary.
3. Verify real valid/expired/revoked tokens, a second unapproved user, sign-out,
   account removal, and operator disable against the actual Auth service. Local
   tests use a fake Auth client and are not production JWT/session evidence.
   In particular, `getUser` is not represented as immediate session revocation;
   add an active-session check if logout must immediately invalidate issued JWTs.
4. Configure the allowlist only after account verification and prove authorized
   dashboard/CRM reads plus negative probes. Review frontend token storage and
   token-free server/access logging before enabling operator access.

The existing five service endpoints and daily jobs are unchanged. No new function,
environment variable, user, database grant, login email or frontend has been
deployed by this preparation. No profile cutover or seller outreach is enabled.

Local validation: all 52 Deno handler tests passed, including ten new operator
cases for identity isolation, missing configuration, metadata spoofing, fixed
read routing, credential/header isolation, upstream errors and bounded CRM data.
CI now discovers all function test files rather than only `security_test.ts`.
