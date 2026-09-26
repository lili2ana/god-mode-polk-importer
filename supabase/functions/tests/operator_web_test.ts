import { buildWeb } from "../../../operator-web/handler.ts";
const origin = "http://127.0.0.1:4317";
const userId = "11111111-1111-4111-8111-111111111111";
const access = "PRIVATE_ACCESS_FIXTURE", refresh = "PRIVATE_REFRESH_FIXTURE";
const counts = {
  properties: 10,
  residential: 6,
  land: 4,
  preliminary_leads: 2,
  hot_preliminary_leads: 1,
};
function eq(a: unknown, b: unknown) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw Error(`Expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
}
function fixture(
  options: {
    enabled?: boolean;
    wrongUser?: boolean;
    logoutFails?: boolean;
    refreshFails?: boolean;
    readStatus?: number;
    readWait?: Promise<void>;
  } = {},
) {
  let time = Date.now();
  const calls = { send: 0, verify: 0, refresh: 0, logout: 0, read: 0 };
  const handler = buildWeb({
    origin,
    operatorId: userId,
    emailEnabled: options.enabled ?? true,
    now: () => time,
    sendCode: async () => {
      calls.send++;
    },
    verifyCode: async () => {
      calls.verify++;
      return {
        userId: options.wrongUser ? "other" : userId,
        access_token: access,
        refresh_token: refresh,
        expires_at: time / 1000 + 120,
      };
    },
    refresh: async (token) => {
      eq(token, refresh);
      calls.refresh++;
      if (options.refreshFails) throw Error(access);
      await Promise.resolve();
      return {
        userId,
        access_token: access,
        refresh_token: refresh,
        expires_at: time / 1000 + 3600,
      };
    },
    logout: async (token) => {
      eq(token, access);
      calls.logout++;
      if (options.logoutFails) throw Error(access);
    },
    read: async (token, view) => {
      eq(token, access);
      calls.read++;
      if (options.readWait) await options.readWait;
      return Response.json(
        view === "dashboard" ? { ok: true, counts } : {
          ok: true,
          count: 1,
          contacts: [{
            name: "<script>alert(1)</script>",
            entity_type: "seller",
            crm_status: "needs_enrichment",
            payload: access,
          }],
        },
        { status: options.readStatus ?? 200 },
      );
    },
  });
  const request = (
    path = "/",
    method = "GET",
    cookie = "",
    body?: string,
    requestOrigin = origin,
  ) =>
    new Request(origin + path, {
      method,
      headers: {
        ...(cookie ? { cookie } : {}),
        ...(method === "POST"
          ? {
            origin: requestOrigin,
            "content-type": "application/x-www-form-urlencoded",
          }
          : {}),
      },
      body,
    });
  async function signIn() {
    const r = await handler(request("/verify-code", "POST", "", "code=123456"));
    eq(r.status, 303);
    const cookie = r.headers.get("set-cookie")!;
    eq(cookie.includes(access), false);
    eq(cookie.includes(refresh), false);
    return cookie.split(";")[0];
  }
  return {
    handler,
    request,
    signIn,
    calls,
    advance: (ms: number) => time += ms,
    options,
  };
}
Deno.test("web: private login page contains no tokens/scripts and email is off by default configuration", async () => {
  const f = fixture({ enabled: false });
  const r = await f.handler(f.request());
  const html = await r.text();
  eq(r.status, 200);
  eq(html.includes("Sign in"), true);
  eq(html.includes(access), false);
  eq(html.includes("<script"), false);
  eq(r.headers.get("cache-control"), "no-store");
  eq(
    r.headers.get("content-security-policy")!.includes(
      "frame-ancestors 'none'",
    ),
    true,
  );
  eq((await f.handler(f.request("/request-code", "POST"))).status, 503);
  eq(f.calls.send, 0);
});
Deno.test("web: email requests require same-origin POST and are throttled", async () => {
  const f = fixture();
  eq((await f.handler(f.request("/request-code"))).status, 404);
  eq(
    (await f.handler(
      f.request(
        "/request-code",
        "POST",
        "",
        undefined,
        "https://attacker.invalid",
      ),
    )).status,
    403,
  );
  eq(f.calls.send, 0);
  eq((await f.handler(f.request("/request-code", "POST"))).status, 200);
  eq((await f.handler(f.request("/request-code", "POST"))).status, 429);
  eq(f.calls.send, 1);
});
Deno.test("web: native form pages preserve Origin without weakening POST checks", async () => {
  const f = fixture();
  eq(
    (await f.handler(f.request())).headers.get("referrer-policy"),
    "same-origin",
  );
  for (const path of ["/request-code", "/verify-code", "/logout"]) {
    for (
      const source of [
        undefined,
        "null",
        "http://localhost:4317",
        "https://evil.test",
      ]
    ) {
      const response = await f.handler(
        new Request(origin + path, {
          method: "POST",
          headers: source === undefined ? {} : { origin: source },
        }),
      );
      eq(response.status, 403);
    }
  }
  eq(f.calls, { send: 0, verify: 0, refresh: 0, logout: 0, read: 0 });
  eq(
    (await f.handler(f.request("/request-code", "POST"))).headers.get(
      "referrer-policy",
    ),
    "same-origin",
  );
  const cookie = await f.signIn();
  for (const path of ["/", "/crm"]) {
    eq(
      (await f.handler(f.request(path, "GET", cookie))).headers.get(
        "referrer-policy",
      ),
      "same-origin",
    );
  }
  const logout = await f.handler(f.request("/logout", "POST", cookie));
  eq(logout.status, 303);
  eq(logout.headers.get("referrer-policy"), "no-referrer");
});
Deno.test("web: wrong host and query credentials rejected without Auth calls", async () => {
  const f = fixture();
  eq((await f.handler(new Request("http://evil.invalid/"))).status, 400);
  eq((await f.handler(f.request("/?token=secret"))).status, 400);
  eq(f.calls.verify, 0);
  eq(f.calls.read, 0);
});
Deno.test("web: verified operator gets opaque HttpOnly cookie and real read rendering", async () => {
  const f = fixture();
  const r = await f.handler(
    f.request("/verify-code", "POST", "", "code=123456"),
  );
  eq(r.status, 303);
  const cookie = r.headers.get("set-cookie")!;
  eq(cookie.includes("HttpOnly; SameSite=Strict"), true);
  const html =
    await (await f.handler(f.request("/", "GET", cookie.split(";")[0]))).text();
  eq(html.includes("Your property workspace"), true);
  eq(html.includes(access), false);
  eq(html.includes(refresh), false);
});
Deno.test("web: mismatched UUID and gateway denial issue no session cookie", async () => {
  for (const options of [{ wrongUser: true }, { readStatus: 403 }]) {
    const f = fixture(options);
    const r = await f.handler(
      f.request("/verify-code", "POST", "", "code=123456"),
    );
    eq(r.status, 403);
    eq(r.headers.get("set-cookie"), null);
  }
});
Deno.test("web: code form rejects malformed duplicate extra and oversized inputs", async () => {
  for (
    const body of [
      "code=123",
      "code=123456&code=654321",
      "code=123456&email=other",
      "code=" + "x".repeat(300),
    ]
  ) {
    const f = fixture();
    eq(
      [400, 413].includes(
        (await f.handler(f.request("/verify-code", "POST", "", body))).status,
      ),
      true,
    );
    eq(f.calls.verify, 0);
  }
});
Deno.test("web: brute-force attempts are bounded", async () => {
  const f = fixture({ wrongUser: true });
  for (let i = 0; i < 5; i++) {
    await f.handler(f.request("/verify-code", "POST", "", "code=123456"));
  }
  eq(
    (await f.handler(f.request("/verify-code", "POST", "", "code=123456")))
      .status,
    429,
  );
  eq(f.calls.verify, 5);
});
Deno.test("web: CRM escapes stored HTML and omits arbitrary payload", async () => {
  const f = fixture();
  const cookie = await f.signIn();
  const html = await (await f.handler(f.request("/crm", "GET", cookie))).text();
  eq(html.includes("&lt;script&gt;"), true);
  eq(html.includes("<script>"), false);
  eq(html.includes(access), false);
});
Deno.test("web: concurrent reads serialize refresh and keep refresh token server-only", async () => {
  const f = fixture();
  const cookie = await f.signIn();
  f.advance(70000);
  const responses = await Promise.all([
    f.handler(f.request("/", "GET", cookie)),
    f.handler(f.request("/crm", "GET", cookie)),
  ]);
  eq(f.calls.refresh, 1);
  for (const r of responses) {
    eq(r.status, 200);
    eq((await r.text()).includes(refresh), false);
  }
});
Deno.test("web: failed refresh invalidates local session", async () => {
  const f = fixture({ refreshFails: true });
  const cookie = await f.signIn();
  f.advance(70000);
  eq((await f.handler(f.request("/", "GET", cookie))).status, 303);
  eq(
    (await (await f.handler(f.request("/", "GET", cookie))).text()).includes(
      "Your private deal workspace",
    ),
    true,
  );
});
Deno.test("web: logout clears local access and reports remote failure honestly", async () => {
  for (const logoutFails of [false, true]) {
    const f = fixture({ logoutFails });
    const cookie = await f.signIn();
    const r = await f.handler(f.request("/logout", "POST", cookie));
    eq(r.status, logoutFails ? 503 : 303);
    eq(r.headers.get("set-cookie")!.includes("Max-Age=0"), true);
    eq(f.calls.logout, 1);
    eq(
      (await (await f.handler(f.request("/", "GET", cookie))).text()).includes(
        "Your private deal workspace",
      ),
      true,
    );
  }
});
Deno.test("web: revoked gateway session and local lifetime deny subsequent reads", async () => {
  const f = fixture();
  const cookie = await f.signIn();
  f.options.readStatus = 401;
  eq((await f.handler(f.request("/", "GET", cookie))).status, 303);
  const g = fixture();
  const c = await g.signIn();
  g.advance(8 * 3600000);
  eq(
    (await (await g.handler(g.request("/", "GET", c))).text()).includes(
      "Your private deal workspace",
    ),
    true,
  );
  eq(g.calls.read, 1);
});
Deno.test("web: logout during an in-flight read suppresses private rendering", async () => {
  const f = fixture();
  const cookie = await f.signIn();
  let release!: () => void;
  f.options.readWait = new Promise<void>((resolve) => release = resolve);
  const pending = f.handler(f.request("/", "GET", cookie));
  await Promise.resolve();
  await f.handler(f.request("/logout", "POST", cookie));
  release();
  const r = await pending;
  eq(r.status, 503);
  eq((await r.text()).includes("Properties"), false);
});
