import { buildEnrollment } from "../../../operator-web/enrollment.ts";
const origin = "http://127.0.0.1:4319",
  userId = "11111111-1111-4111-8111-111111111111";
function eq(a: unknown, b: unknown) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw Error(`Mismatch ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
  }
}
function fixture(
  options: {
    wait?: Promise<void>;
    revokeFail?: boolean;
    recordFail?: boolean;
    unsafe?: boolean;
    expiredToken?: boolean;
    startWait?: Promise<void>;
  } = {},
) {
  let time = Date.now(), callback = "";
  const calls = {
    start: 0,
    exchange: 0,
    revoke: 0,
    dispose: 0,
    records: [] as unknown[],
  };
  const handler = buildEnrollment({
    origin,
    now: () => time,
    start: async (url) => {
      calls.start++;
      callback = url;
      if (options.startWait) await options.startWait;
      const authorize = new URL(
        "https://bnsmnztxkqmphvbikaxh.supabase.co/auth/v1/authorize",
      );
      for (
        const [k, v] of Object.entries({
          provider: "github",
          redirect_to: url,
          scopes: options.unsafe ? "repo" : "user:email",
          code_challenge: "a".repeat(43),
          code_challenge_method: "s256",
        })
      ) authorize.searchParams.set(k, v);
      return {
        url: authorize.href,
        dispose: () => {
          calls.dispose++;
        },
        exchange: async () => {
          calls.exchange++;
          if (options.wait) await options.wait;
          return {
            userId,
            access_token: "access-fixture",
            refresh_token: "refresh-fixture",
            expires_at: time / 1000 + (options.expiredToken ? -1 : 3600),
          };
        },
      };
    },
    revoke: async () => {
      calls.revoke++;
      if (options.revokeFail) throw Error("private error");
    },
    record: async (o) => {
      if (options.recordFail) throw Error("disk error");
      calls.records.push(o);
    },
  });
  const post = (path: string, source = origin) =>
    handler(
      new Request(origin + path, {
        method: "POST",
        headers: { origin: source },
      }),
    );
  const start = async () => {
    const response = await post("/enroll/start");
    const cookie = (response.headers.get("set-cookie") ?? "").split(";")[0];
    return {
      response,
      cookie,
      url: callback + "&code=11111111-2222-4333-8444-555555555555",
    };
  };
  return {
    handler,
    post,
    start,
    calls,
    advance: (ms: number) => {
      time += ms;
    },
    callback: () => callback,
  };
}
Deno.test("enrollment records UUID only after revocation and never issues workspace cookie", async () => {
  const f = fixture(), s = await f.start();
  eq(s.response.status, 200);
  const r = await f.handler(
    new Request(s.url, { headers: { cookie: s.cookie } }),
  );
  eq(r.status, 303);
  eq(r.headers.get("location"), "/");
  eq(f.calls.revoke, 1);
  eq(f.calls.records.length, 1);
  eq(Object.keys(f.calls.records[0] as object).sort(), [
    "authorizationGranted",
    "observedAt",
    "remoteLogoutConfirmed",
    "userId",
  ]);
  eq((f.calls.records[0] as any).authorizationGranted, false);
  eq(r.headers.get("set-cookie")?.includes("Max-Age=0"), true);
  eq((await r.text()).includes("access-fixture"), false);
  eq((await f.post("/enroll/start")).status, 409);
  eq((await f.handler(new Request(origin + "/crm"))).status, 404);
});
Deno.test("enrollment rejects cross-origin start and foreign host", async () => {
  const f = fixture();
  eq((await f.post("/enroll/start", "https://evil.test")).status, 403);
  eq(
    (await f.handler(
      new Request(origin + "/", { headers: { host: "evil.test" } }),
    )).status,
    400,
  );
  eq(f.calls.start, 0);
});
Deno.test("enrollment requires bound single cookie and callback state", async () => {
  const f = fixture(), s = await f.start();
  for (
    const cookie of ["", s.cookie + "; " + s.cookie, "godmode_enrollment=wrong"]
  ) {
    eq(
      (await f.handler(new Request(s.url, { headers: { cookie } }))).status,
      303,
    );
  }
  eq(f.calls.exchange, 0);
  eq(f.calls.records.length, 0);
});
Deno.test("enrollment consumes a callback once including concurrent replay", async () => {
  let release!: () => void;
  const wait = new Promise<void>((r) => release = r);
  const f = fixture({ wait }), s = await f.start();
  const first = f.handler(
    new Request(s.url, { headers: { cookie: s.cookie } }),
  );
  await Promise.resolve();
  eq(
    (await f.handler(new Request(s.url, { headers: { cookie: s.cookie } })))
      .status,
    303,
  );
  release();
  await first;
  eq(f.calls.exchange, 1);
  eq(f.calls.records.length, 1);
  await f.handler(new Request(s.url, { headers: { cookie: s.cookie } }));
  eq(f.calls.exchange, 1);
});
Deno.test("enrollment cancellation during exchange revokes but does not record", async () => {
  let release!: () => void;
  const wait = new Promise<void>((r) => release = r);
  const f = fixture({ wait }), s = await f.start();
  const first = f.handler(
    new Request(s.url, { headers: { cookie: s.cookie } }),
  );
  await Promise.resolve();
  await f.post("/enroll/cancel");
  release();
  await first;
  eq(f.calls.revoke, 1);
  eq(f.calls.records.length, 0);
});
Deno.test("enrollment pending start is reserved and cancellation disposes late PKCE state", async () => {
  let release!: () => void;
  const startWait = new Promise<void>((r) => release = r);
  const f = fixture({ startWait });
  const first = f.post("/enroll/start");
  await Promise.resolve();
  f.advance(11000);
  eq((await f.post("/enroll/start")).status, 429);
  await f.post("/enroll/cancel");
  release();
  eq((await first).status, 303);
  eq(f.calls.dispose, 1);
});
Deno.test("enrollment expires callback before exchange", async () => {
  const f = fixture(), s = await f.start();
  f.advance(300001);
  await f.handler(new Request(s.url, { headers: { cookie: s.cookie } }));
  eq(f.calls.exchange, 0);
  eq(f.calls.records.length, 0);
});
Deno.test("enrollment rejects expired returned tokens and revokes them", async () => {
  const f = fixture({ expiredToken: true }), s = await f.start();
  await f.handler(new Request(s.url, { headers: { cookie: s.cookie } }));
  eq(f.calls.revoke, 1);
  eq(f.calls.records.length, 0);
});
Deno.test("enrollment requires remote logout success before evidence", async () => {
  const f = fixture({ revokeFail: true }), s = await f.start();
  const r = await f.handler(
    new Request(s.url, { headers: { cookie: s.cookie } }),
  );
  eq(r.headers.get("location"), "/failed");
  eq(f.calls.records.length, 0);
  eq((await r.text()).includes("private error"), false);
});
Deno.test("enrollment record failure grants nothing and reports failure", async () => {
  const f = fixture({ recordFail: true }), s = await f.start();
  const r = await f.handler(
    new Request(s.url, { headers: { cookie: s.cookie } }),
  );
  eq(r.headers.get("location"), "/failed");
  eq(f.calls.revoke, 1);
  eq(f.calls.records.length, 0);
});
Deno.test("enrollment rejects extra scope and disposes provider state", async () => {
  const f = fixture({ unsafe: true }), s = await f.start();
  eq(s.response.status, 303);
  eq(f.calls.dispose, 1);
  eq(f.calls.exchange, 0);
});
Deno.test("enrollment rejects duplicate code and unexpected callback parameters", async () => {
  for (const extra of ["&code=duplicate", "&provider=azure"]) {
    const f = fixture(), s = await f.start();
    await f.handler(
      new Request(s.url + extra, { headers: { cookie: s.cookie } }),
    );
    eq(f.calls.exchange, 0);
    eq(f.calls.records.length, 0);
  }
});
