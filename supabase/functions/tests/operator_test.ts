import { buildHandler } from "../god-mode-operator-read/handler.ts";
const id = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const secret = "sb_secret_fixture_server_only";
const sessionId = "33333333-3333-4333-8333-333333333333";
function jwt(overrides: Record<string, unknown> = {}) {
  const claims = {
    sub: id,
    session_id: sessionId,
    role: "authenticated",
    aud: "authenticated",
    iss: "https://bnsmnztxkqmphvbikaxh.supabase.co/auth/v1",
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...overrides,
  };
  // Fake Auth verifies these fixtures; not a signed production JWT.
  return `fixture.${
    btoa(JSON.stringify(claims)).replace(/=/g, "").replace(/\+/g, "-").replace(
      /\//g,
      "_",
    )
  }.fixture`;
}
const validToken = jwt();
const counts = {
  properties: 10,
  residential: 6,
  land: 4,
  preliminary_leads: 3,
  hot_preliminary_leads: 1,
};
function eq(a: unknown, b: unknown) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw Error(`Expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
}
function fixture(
  options: {
    user?: any;
    authError?: boolean;
    throwAuth?: boolean;
    env?: Record<string, string>;
    reply?: () => Response;
    fetchThrow?: boolean;
    sessionActive?: unknown;
    sessionError?: boolean;
    sessionThrow?: boolean;
  } = {},
) {
  const authTokens: string[] = [],
    sessionCalls: unknown[] = [],
    reads: Array<{ url: string; init?: RequestInit }> = [];
  const env: Record<string, string> = {
    SUPABASE_URL: "https://bnsmnztxkqmphvbikaxh.supabase.co",
    SUPABASE_SECRET_KEYS: JSON.stringify({ default: secret }),
    SUPABASE_SERVICE_ROLE_KEY: "legacy-server-secret",
    GOD_MODE_OPERATOR_USER_IDS: id,
    ...options.env,
  };
  const h = buildHandler({
    env: (n) => env[n],
    createClient: (_url, _key, opts) => {
      eq(opts.auth.persistSession, false);
      return {
        auth: {
          getUser: async (token: string) => {
            authTokens.push(token);
            if (options.throwAuth) throw Error(secret);
            return {
              data: {
                user: options.user === undefined
                  ? {
                    id,
                    is_anonymous: false,
                    email_confirmed_at: "2026-09-22T00:00:00Z",
                  }
                  : options.user,
              },
              error: options.authError ? { message: secret } : null,
            };
          },
        },
        rpc: async (name: string, args: unknown) => {
          sessionCalls.push({ name, args });
          if (options.sessionThrow) throw Error(secret);
          return {
            data: options.sessionActive === undefined
              ? true
              : options.sessionActive,
            error: options.sessionError ? { message: secret } : null,
          };
        },
        from: () => {
          throw Error("No direct business query permitted");
        },
      };
    },
    fetch: async (input, init) => {
      reads.push({ url: String(input), init });
      if (options.fetchThrow) throw Error(secret);
      return options.reply
        ? options.reply()
        : Response.json({ ok: true, counts, secret_leak: secret });
    },
  });
  return { h, authTokens, sessionCalls, reads, env };
}
const request = (
  query = "view=dashboard",
  token = validToken,
  method = "GET",
) =>
  new Request(`https://example.invalid?${query}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      cookie: "must-not-forward=yes",
      "x-god-mode-token": "must-not-forward",
    },
  });
Deno.test("operator: missing token and machine credentials are not user login", async () => {
  const f = fixture();
  for (
    const headers of [{}, { apikey: secret }, {
      "x-god-mode-token": "a".repeat(64),
    }]
  ) {
    eq(
      (await f.h(
        new Request("https://example.invalid", {
          headers: headers as Record<string, string>,
        }),
      )).status,
      401,
    );
  }
  for (const token of [secret, "legacy-server-secret", "x".repeat(8193)]) {
    eq((await f.h(request("", token))).status, 401);
  }
  eq(f.authTokens, []);
  eq(f.reads, []);
});
Deno.test("operator: invalid or absent allowlist and wrong project fail before Auth or data reads", async () => {
  const cases: Array<Record<string, string>> = [
    { GOD_MODE_OPERATOR_USER_IDS: "" },
    { GOD_MODE_OPERATOR_USER_IDS: "someone@example.invalid" },
    { SUPABASE_URL: "https://attacker.invalid" },
    { SUPABASE_SECRET_KEYS: "bad JSON" },
  ];
  for (const env of cases) {
    const f = fixture({ env });
    eq((await f.h(request())).status, 503);
    eq(f.authTokens, []);
    eq(f.reads, []);
  }
});
Deno.test("operator: forged/expired/rejected Auth token cannot read business data", async () => {
  for (
    const options of [{ authError: true }, { user: null }, { throwAuth: true }]
  ) {
    const f = fixture(options);
    const r = await f.h(request());
    eq([401, 503].includes(r.status), true);
    eq(f.reads, []);
    eq((await r.text()).includes(secret), false);
  }
});
Deno.test("operator: valid but unapproved/anonymous user and editable metadata are denied", async () => {
  for (
    const user of [
      {
        id: other,
        is_anonymous: false,
        user_metadata: { role: "admin", operator: true },
      },
      { id, is_anonymous: true },
      { id },
    ]
  ) {
    const f = fixture({ user });
    eq((await f.h(request())).status, 403);
    eq(f.reads, []);
  }
});
Deno.test("operator: dashboard route verifies identity first and exposes no backend credential", async () => {
  const f = fixture();
  const r = await f.h(request());
  const b = await r.json();
  eq(f.authTokens, [validToken]);
  eq(f.sessionCalls, [{
    name: "god_mode_operator_session_active",
    args: {
      requested_user: id,
      requested_session: sessionId,
    },
  }]);
  eq(f.reads.length, 1);
  eq(
    f.reads[0].url,
    "https://bnsmnztxkqmphvbikaxh.supabase.co/functions/v1/god-mode-dashboard?format=json",
  );
  eq(f.reads[0].init?.method, "GET");
  eq(f.reads[0].init?.redirect, "error");
  eq(!!f.reads[0].init?.signal, true);
  eq(f.reads[0].init?.headers, { accept: "application/json", apikey: secret });
  eq(b, {
    ok: true,
    counts,
    acquisition_authorized: false,
    outreach_authorized: false,
  });
  eq(r.headers.get("cache-control"), "no-store");
  eq(r.headers.get("access-control-allow-origin"), null);
});
Deno.test("operator: CRM uses fixed read-only destination and strips upstream headers", async () => {
  const f = fixture({
    reply: () =>
      Response.json({
        ok: true,
        count: 1,
        contacts: [{ id: "synthetic-contact" }],
      }, {
        headers: {
          "set-cookie": "do-not-forward",
          "access-control-allow-origin": "*",
        },
      }),
  });
  const r = await f.h(request("view=crm&entity=seller"));
  eq(
    f.reads[0].url,
    "https://bnsmnztxkqmphvbikaxh.supabase.co/functions/v1/god-mode-crm-feed?entity=seller",
  );
  eq(await r.json(), {
    ok: true,
    count: 1,
    contacts: [{ id: "synthetic-contact" }],
    outreach_authorized: false,
  });
  eq(r.headers.get("set-cookie"), null);
  eq(r.headers.get("access-control-allow-origin"), null);
});
Deno.test("operator: arbitrary destinations, worker routes and writes are rejected", async () => {
  const f = fixture();
  for (
    const q of [
      "view=god-mode-dd-worker",
      "view=https://attacker.invalid",
      "view=crm&entity=admin",
      "url=https://attacker.invalid",
      "view=dashboard&token=secret",
    ]
  ) eq((await f.h(request(q))).status, 400);
  for (const method of ["POST", "PUT", "DELETE", "OPTIONS"]) {
    eq((await f.h(request("", undefined, method))).status, 405);
  }
  eq(f.authTokens, []);
  eq(f.reads, []);
});
Deno.test("operator: upstream failure/redirect/HTML/malformed data fail closed without private errors", async () => {
  for (
    const reply of [
      () => new Response(secret, { status: 500 }),
      () =>
        new Response(null, {
          status: 302,
          headers: { location: "https://attacker.invalid" },
        }),
      () => new Response(secret, { headers: { "content-type": "text/html" } }),
      () => Response.json({ ok: false, error: secret }),
      () => Response.json({ ok: true, counts: {} }),
    ]
  ) {
    const f = fixture({ reply });
    const r = await f.h(request());
    eq(r.status, 502);
    eq((await r.text()).includes(secret), false);
  }
  const f = fixture({ fetchThrow: true });
  const r = await f.h(request());
  eq(r.status, 503);
  eq((await r.text()).includes(secret), false);
});
Deno.test("operator: oversized/malformed CRM results are not exposed", async () => {
  for (
    const body of [{ ok: true, count: 201, contacts: Array(201).fill({}) }, {
      ok: true,
      count: 2,
      contacts: [],
    }, { ok: true, count: 0, contacts: null }]
  ) {
    const f = fixture({ reply: () => Response.json(body) });
    eq((await f.h(request("view=crm"))).status, 502);
  }
});
Deno.test("operator: removing approved UUID denies the next request", async () => {
  const f = fixture();
  eq((await f.h(request())).status, 200);
  f.env.GOD_MODE_OPERATOR_USER_IDS = other;
  eq((await f.h(request())).status, 403);
  eq(f.reads.length, 1);
});
Deno.test("operator: unconfirmed email is denied before session lookup", async () => {
  const f = fixture({
    user: { id, is_anonymous: false, email_confirmed_at: null },
  });
  eq((await f.h(request())).status, 403);
  eq(f.sessionCalls, []);
  eq(f.reads, []);
});
Deno.test("operator: malformed and mismatched claims cannot reach session lookup", async () => {
  const tokens = [
    "user.access.token",
    "a.e30.b.c",
    "a.bnVsbA.b",
    jwt({ sub: other }),
    jwt({ session_id: "invalid" }),
    jwt({ session_id: null }),
    jwt({ exp: 0 }),
    jwt({ exp: "99999999999" }),
    jwt({ iss: "https://attacker.invalid/auth/v1" }),
    jwt({ aud: "anon" }),
    jwt({ role: "service_role" }),
  ];
  for (const token of tokens) {
    const f = fixture();
    eq((await f.h(request("", token))).status, 401);
    eq(f.sessionCalls, []);
    eq(f.reads, []);
  }
});
Deno.test("operator: rejected Auth never invokes privileged session lookup", async () => {
  const f = fixture({ authError: true });
  eq((await f.h(request())).status, 401);
  eq(f.sessionCalls, []);
  eq(f.reads, []);
});
Deno.test("operator: missing/revoked session and nonboolean results fail closed", async () => {
  for (const sessionActive of [false, null, "true", [], { active: true }]) {
    const f = fixture({ sessionActive });
    eq((await f.h(request())).status, 401);
    eq(f.reads, []);
  }
});
Deno.test("operator: session lookup errors deny without leaking credentials", async () => {
  for (const options of [{ sessionError: true }, { sessionThrow: true }]) {
    const f = fixture(options);
    const r = await f.h(request());
    eq(r.status, 503);
    eq((await r.text()).includes(secret), false);
    eq(f.reads, []);
  }
});
Deno.test("operator: session revocation is checked again on the next read", async () => {
  const options = { sessionActive: true };
  const f = fixture(options);
  eq((await f.h(request())).status, 200);
  options.sessionActive = false;
  eq((await f.h(request())).status, 401);
  eq(f.sessionCalls.length, 2);
  eq(f.reads.length, 1);
});
