# Private operator web interface — READY locally, not production login

This single-process Deno server supplies a sign-in form, one-time-code form,
dashboard counts and a capped CRM review table. It listens only on 127.0.0.1.
Background import and DD schedules run separately and do not depend on this
browser session or the local web server.

Auth access/refresh tokens stay in server memory. The browser gets only a random,
HttpOnly, SameSite=Strict session cookie. No client JavaScript, localStorage,
access/refresh-token URLs or machine credentials are used. The web server uses only a
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
GOD_MODE_MICROSOFT_LOGIN_ENABLED=false
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
The operator has no business domain or sending service. That question and the
operator email selection are resolved. A Microsoft sign-in alternative is now
implemented locally; it is not configured or verified against hosted Auth.

References: [passwordless email](https://supabase.com/docs/guides/auth/auth-email-passwordless),
[SMTP setup and default-sender restrictions](https://supabase.com/docs/guides/auth/auth-smtp).

## Microsoft alternative — READY locally, disabled by default

Microsoft sign-in avoids our sending a login email. It does not require the
operator to buy a domain, but does require an authorized Microsoft Entra app
registration, suitable tenant access and Supabase Azure-provider configuration.
None has been created, changed or proved available by this release. Do not assume
that an existing personal Microsoft account supplies an eligible Entra tenant.

Before enabling this path, configure the app's supported personal-account type,
Supabase's documented `consumers` tenant URL where appropriate, and the app's
exact HTTPS Supabase Auth callback. Keep the client secret only in the provider's
server configuration. Follow the documented verified-email claim setup, including
`email` and `xms_edov`; request the `email` scope. Configure Supabase's frontend
redirect allowlist narrowly for the reviewed local `/oauth/callback?flow=...`
route, and test the exact match behavior. No arbitrary callback is accepted here.

The current entrypoint still requires an actual approved Auth UUID. Initial
identity enrollment and ownership verification must happen before authorizing
that UUID on either service; this release supplies no enrollment bypass and does
not auto-confirm an email. A successful provider callback alone cannot grant
access: the private gateway must independently approve the user and active session.

Each same-origin sign-in POST creates its own server-memory PKCE verifier and a
five-minute opaque HttpOnly SameSite=Lax flow cookie. Only this temporary cookie
uses Lax for the cross-site callback. The callback must match that browser flow;
duplicates, expiry, wrong UUID, logout during exchange and failed gateway reads
deny access. The resulting workspace cookie stays HttpOnly SameSite=Strict.

The UI uses an explicit link to the fixed Supabase authorize URL, then a clean
same-origin landing page with an `Open dashboard and CRM` link after callback.
This avoids redirect/CSP and Strict-cookie ambiguity. A short-lived authorization
code and opaque flow identifier necessarily arrive in the callback query; they
are never echoed into HTML and are redirected to a clean URL. Never enable access
logging of callback queries. Access/refresh tokens and the PKCE verifier stay on
the server. Microsoft provider tokens are not returned to the application session.
Pending verifiers are cleared on completion, cancellation or the next request
after expiry. No browser storage or client JavaScript is involved.

Eleven local tests exercise flow isolation, replay, cancellation, malformed inputs,
authorization and the actual pinned SDK's PKCE exchange using a fake HTTP
transport, including confirmed-user lookup failures. These are not Microsoft
consent, hosted Auth or real-browser evidence.
Complete real sign-in, refresh, logout, revocation and unapproved-user checks
before deployment. Both login enable flags remain false by default.

References: [Supabase Microsoft setup](https://supabase.com/docs/guides/auth/social-login/auth-azure),
[PKCE flow](https://supabase.com/docs/guides/auth/sessions/pkce-flow).

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
