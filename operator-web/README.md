# Private operator web interface — READY locally, not production login

This single-process Deno server supplies a sign-in form, one-time-code form,
dashboard counts and a capped CRM review table. It listens only on 127.0.0.1.
Background import and DD schedules run separately and do not depend on this
browser session or the local web server.

Auth access/refresh tokens stay in server memory. The browser gets only a random,
HttpOnly, SameSite=Strict session cookie. No client JavaScript, localStorage,
token-bearing URLs or machine credentials are used. The web server uses only a
publishable key and the signed-in user's token. The private read gateway enforces
the approved UUID and active Auth session on every read.

POST requests require the exact configured Origin. Code requests are limited to
one per minute and verification to five attempts per minute, process-wide for
this single-operator application. Forms are bounded to 256 bytes. Refresh calls
for one session are serialized; local sessions expire after eight hours and are
lost on server restart. Hosted/multi-process operation requires a separate,
reviewed TLS/origin/session-store deployment; do not expose this local listener
through an ad-hoc public tunnel.

## Configuration and execution

Set the approved operator email and **actual Auth UUID**, the project's
`sb_publishable_` key, and optionally the origin (default below) in a private
environment file outside version control. Never use a service key here.

```
GOD_MODE_WEB_ORIGIN=http://127.0.0.1:4317
GOD_MODE_OPERATOR_EMAIL=<approved operator email>
GOD_MODE_OPERATOR_USER_ID=<actual approved Auth UUID>
SUPABASE_PUBLISHABLE_KEY=<project publishable key>
GOD_MODE_LOGIN_EMAIL_ENABLED=false
```

Run from the repository root with Deno 2.5.2:

```
deno run --lock=operator-web/deno.lock --frozen --env-file=.polk_import/operator-web.env --allow-env --allow-net=127.0.0.1:4317,bnsmnztxkqmphvbikaxh.supabase.co operator-web/server.ts
```

The only action that requests email is a same-origin form POST to `/request-code`
while the email-enabled setting is true. Requests target the configured email;
there is no arbitrary-recipient input and no automatic signup. `/verify-code`
must return the approved UUID and successfully call the deployed operator gateway
before an application session is issued. No accounts are created by this server.

Before enabling email, provision the intended unconfirmed Auth account without
auto-confirming mailbox ownership, configure the actual UUID on both services,
and complete gateway deployment checks. Verify the sender and a template that
contains the six-digit OTP (not just a magic link). The account must prove mailbox
ownership through Auth before receiving an operator session. Do not email a user
from an agent tool without authorization.

## Deployment blocker observed September 22

Read-only dashboard inspection showed custom SMTP disabled and template editing
unavailable until custom SMTP is connected. Supabase's default sender is limited
to project-team email addresses and is not a production mail service. No SMTP
credentials were accessed, settings changed or email sent during inspection.
The operator has been asked whether an existing business domain/sending service
is available. This is separate from the already-resolved operator email selection.

References: [passwordless email](https://supabase.com/docs/guides/auth/auth-email-passwordless),
[SMTP setup and default-sender restrictions](https://supabase.com/docs/guides/auth/auth-smtp).

## Verification and limitations

Thirteen local web-handler tests cover sign-in, session renewal, sign-out, UUID
isolation, cross-origin rejection, throttling, bounded inputs, escaping and token
isolation. These use fake Auth/read adapters: they do not establish email delivery
or production browser authentication. The production adapter type-checks against
pinned supabase-js 2.57.4. Server restart requires another login; it does not stop
the independent cloud jobs.

Sign-out removes the local session immediately, then requests remote revocation.
A remote failure is shown as unconfirmed revocation, never as verified logout.
An already-authorized backend request cannot be cancelled by this session check;
the web response suppresses private rendering if the local session was removed.
No underwriting, outreach or operational-profile cutover is enabled by this UI.
