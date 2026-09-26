import { buildWeb } from "../../../operator-web/handler.ts";
import { microsoftLogin } from "../../../operator-web/microsoft.ts";
import { oauthLogin } from "../../../operator-web/oauth.ts";

for (const provider of ["azure", "github"] as const) {
  const beginLogin = (key: string, request: typeof fetch) =>
    provider === "azure"
      ? microsoftLogin(key, request)
      : oauthLogin(provider, key, request);
  const origin = "http://127.0.0.1:4317";
  const project = "https://bnsmnztxkqmphvbikaxh.supabase.co";
  const operator = "11111111-1111-4111-8111-111111111111";
  const access = "PRIVATE_ACCESS_FIXTURE", refresh = "PRIVATE_REFRESH_FIXTURE";
  const code = "11111111-2222-4333-8444-555555555555";
  function eq(a: unknown, b: unknown) {
    if (JSON.stringify(a) !== JSON.stringify(b)) {
      throw Error(`Expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
    }
  }
  function fixture(
    options: {
      wrongUser?: boolean;
      unsafeRedirect?: boolean;
      gatewayDenied?: boolean;
      wait?: Promise<void>;
      readWait?: Promise<void>;
      disabled?: boolean;
      tamper?: (url: URL) => void;
    } = {},
  ) {
    let time = Date.now(), callback = "";
    const calls = { start: 0, exchange: 0, dispose: 0, read: 0, email: 0 };
    const tokens = () => ({
      access_token: access,
      refresh_token: refresh,
      expires_at: time / 1000 + 3600,
      userId: options.wrongUser
        ? "22222222-2222-4222-8222-222222222222"
        : operator,
    });
    const handler = buildWeb({
      origin,
      operatorId: operator,
      emailEnabled: false,
      now: () => time,
      sendCode: async () => {
        calls.email++;
      },
      verifyCode: async () => tokens(),
      refresh: async () => tokens(),
      logout: async () => {},
      read: async () => {
        calls.read++;
        if (options.readWait) await options.readWait;
        return Response.json({
          ok: !options.gatewayDenied,
          counts: {
            properties: 1,
            residential: 1,
            land: 0,
            preliminary_leads: 0,
            hot_preliminary_leads: 0,
          },
        }, { status: options.gatewayDenied ? 403 : 200 });
      },
      [provider === "github" ? "github" : "microsoft"]: options.disabled
        ? undefined
        : async (target: string) => {
          calls.start++;
          callback = target;
          const url = new URL(project + "/auth/v1/authorize");
          url.search = new URLSearchParams({
            provider,
            scopes: provider === "github" ? "user:email" : "email",
            redirect_to: target,
            code_challenge: "a".repeat(43),
            code_challenge_method: "s256",
          }).toString();
          options.tamper?.(url);
          return {
            url: options.unsafeRedirect
              ? "https://attacker.invalid/"
              : url.href,
            dispose: () => {
              calls.dispose++;
            },
            exchange: async (value: string) => {
              eq(value, code);
              calls.exchange++;
              if (options.wait) await options.wait;
              return tokens();
            },
          };
        },
    });
    const req = (path: string, method = "GET", cookie = "", source = origin) =>
      new Request(origin + path, {
        method,
        headers: { cookie, ...(method === "POST" ? { origin: source } : {}) },
      });
    async function start() {
      const response = await handler(req("/oauth/start", "POST"));
      eq(response.status, 200);
      const cookie = response.headers.get("set-cookie")!;
      eq(cookie.includes("HttpOnly; SameSite=Lax; Max-Age=300"), true);
      return {
        cookie: cookie.split(";")[0],
        callback: new URL(callback).pathname + new URL(callback).search,
      };
    }
    return { handler, req, start, calls, advance: (ms: number) => time += ms };
  }
  Deno.test(
    provider +
      ": login UI identifies selected provider and rejects scope/provider changes",
    async () => {
      const f = fixture();
      const landing = await f.handler(f.req("/"));
      eq(landing.headers.get("referrer-policy"), "same-origin");
      const html = await landing.text();
      eq(
        html.includes(
          provider === "github"
            ? "Continue with GitHub"
            : "Continue with Microsoft",
        ),
        true,
      );
      for (
        const tamper of [
          (url: URL) => url.searchParams.set("scopes", "repo"),
          (url: URL) => url.searchParams.append("scopes", "user:email"),
          (url: URL) =>
            url.searchParams.set(
              "provider",
              provider === "github" ? "azure" : "github",
            ),
          (url: URL) => url.searchParams.set("scope", "repo"),
          (url: URL) =>
            url.searchParams.set("redirect_to", "https://attacker.invalid/"),
        ]
      ) {
        const unsafe = fixture({ tamper });
        eq(
          (await unsafe.handler(unsafe.req("/oauth/start", "POST"))).status,
          503,
        );
        eq(unsafe.calls.dispose, 1);
        eq(unsafe.calls.exchange, 0);
      }
    },
  );
  Deno.test(
    provider +
      ": callback binds browser flow, exchanges once, and issues only opaque Strict session",
    async () => {
      const f = fixture();
      const flow = await f.start();
      const result = await f.handler(
        f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
      );
      eq(result.status, 303);
      eq(result.headers.get("location"), "/signed-in");
      const cookies = result.headers.getSetCookie();
      const session = cookies.find((x) => x.startsWith("godmode_local="))!;
      eq(session.includes("HttpOnly; SameSite=Strict"), true);
      eq(
        cookies.some((x) =>
          x.includes(access) || x.includes(refresh) || x.includes(code)
        ),
        false,
      );
      eq((await f.handler(f.req("/signed-in"))).status, 200);
      const privatePage = await f.handler(
        f.req("/", "GET", session.split(";")[0]),
      );
      eq((await privatePage.text()).includes("Your property workspace"), true);
      await f.handler(
        f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
      );
      eq(f.calls.exchange, 1);
      eq(f.calls.dispose, 1);
      eq(f.calls.email, 0);
    },
  );
  Deno.test(
    provider +
      ": expiry while exchange or authorization is pending cannot issue a session",
    async () => {
      for (const stage of ["exchange", "read"]) {
        let release!: () => void;
        const wait = new Promise<void>((r) => release = r);
        const f = fixture(stage === "exchange" ? { wait } : { readWait: wait });
        const flow = await f.start();
        const pending = f.handler(
          f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
        );
        await Promise.resolve();
        f.advance(300001);
        release();
        const result = await pending;
        eq(result.headers.get("location"), "/signin-error");
        eq(
          result.headers.getSetCookie().some((x) =>
            x.startsWith("godmode_local=")
          ),
          false,
        );
      }
    },
  );
  Deno.test(
    provider +
      " SDK: unconfirmed, anonymous and failed confirmed-user lookups fail closed",
    async () => {
      for (const state of ["unconfirmed", "anonymous", "error"]) {
        let lookups = 0, revocations = 0;
        const request: typeof fetch = async (input, init) => {
          const url = new URL(String(input));
          if (url.pathname.endsWith("/logout")) {
            eq(url.href, project + "/auth/v1/logout?scope=local");
            eq(init?.method, "POST");
            eq(
              new Headers(init?.headers).get("authorization"),
              `Bearer ${access}`,
            );
            eq(
              new Headers(init?.headers).get("apikey"),
              "sb_publishable_fixture",
            );
            revocations++;
            return new Response(null, { status: 204 });
          }
          if (url.pathname.endsWith("/token")) {
            return Response.json({
              access_token: access,
              refresh_token: refresh,
              expires_in: 3600,
              token_type: "bearer",
              user: { id: operator },
            });
          }
          lookups++;
          return state === "error"
            ? Response.json({ message: "rejected" }, { status: 401 })
            : Response.json({
              id: operator,
              email_confirmed_at: state === "unconfirmed"
                ? null
                : "2026-09-22T00:00:00Z",
              is_anonymous: state === "anonymous",
            });
        };
        const flow = await beginLogin("sb_publishable_fixture", request)(
          origin + "/oauth/callback?flow=test",
        );
        let denied = false;
        try {
          await flow.exchange(code);
        } catch {
          denied = true;
        } finally {
          flow.dispose();
        }
        eq(denied, true);
        eq(lookups, 1);
        eq(revocations, 1);
      }
    },
  );
  Deno.test(
    provider +
      " SDK: rejected identity reports unconfirmed cleanup without leaking errors",
    async () => {
      for (const failure of ["http", "transport"]) {
        let revocations = 0;
        const request: typeof fetch = async (input) => {
          const url = new URL(String(input));
          if (url.pathname.endsWith("/token")) {
            return Response.json({
              access_token: access,
              refresh_token: refresh,
              expires_in: 3600,
              token_type: "bearer",
              user: { id: operator },
            });
          }
          if (url.pathname.endsWith("/user")) {
            return Response.json({ id: operator, email_confirmed_at: null });
          }
          eq(url.href, project + "/auth/v1/logout?scope=local");
          revocations++;
          if (failure === "transport") throw Error(access + refresh);
          return new Response(access + refresh, { status: 503 });
        };
        const flow = await beginLogin("sb_publishable_fixture", request)(
          origin + "/oauth/callback?flow=test",
        );
        let message = "";
        try {
          await flow.exchange(code);
        } catch (error) {
          message = (error as Error).message;
        } finally {
          flow.dispose();
        }
        eq(
          message,
          "Provider identity rejected; remote revocation unconfirmed",
        );
        eq(revocations, 1);
        eq(message.includes(access) || message.includes(refresh), false);
      }
    },
  );
  Deno.test(
    provider +
      ": disabled and cross-origin starts never initiate provider or email",
    async () => {
      const off = fixture({ disabled: true });
      eq((await off.handler(off.req("/oauth/start", "POST"))).status, 503);
      const f = fixture();
      eq(
        (await f.handler(
          f.req("/oauth/start", "POST", "", "https://attacker.invalid"),
        )).status,
        403,
      );
      eq((await f.handler(f.req("/oauth/start"))).status, 404);
      eq(f.calls.start, 0);
      eq(off.calls.start, 0);
    },
  );
  Deno.test(
    provider +
      ": callback without browser cookie or with another browser flow cannot exchange",
    async () => {
      const f = fixture();
      const first = await f.start();
      f.advance(11000);
      const second = await f.start();
      for (
        const cookie of ["", second.cookie, first.cookie + "; " + first.cookie]
      ) {
        const r = await f.handler(
          f.req(first.callback + "&code=" + code, "GET", cookie),
        );
        eq(r.headers.get("location"), "/signin-error");
      }
      eq(f.calls.exchange, 0);
    },
  );
  Deno.test(
    provider + ": expired flow and logout cancel pending exchanges",
    async () => {
      for (const logout of [false, true]) {
        const f = fixture();
        const flow = await f.start();
        if (logout) await f.handler(f.req("/logout", "POST", flow.cookie));
        else f.advance(300001);
        eq(
          (await f.handler(
            f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
          )).headers.get("location"),
          "/signin-error",
        );
        eq(f.calls.exchange, 0);
      }
    },
  );
  Deno.test(
    provider +
      ": duplicate codes, arbitrary next URL, errors and oversized codes are stripped without exchange",
    async () => {
      for (
        const suffix of [
          "&code=" + code + "&code=" + code,
          "&code=" + code + "&next=https://attacker.invalid",
          "&error=access_denied&error_description=" + access,
          "&code=" + "a".repeat(2049),
        ]
      ) {
        const f = fixture();
        const flow = await f.start();
        const r = await f.handler(
          f.req(flow.callback + suffix, "GET", flow.cookie),
        );
        eq(r.headers.get("location"), "/signin-error");
        eq(f.calls.exchange, 0);
        eq((await r.text()).includes(access), false);
        eq(f.calls.dispose, 1);
      }
    },
  );
  Deno.test(
    provider +
      ": wrong verified UUID and gateway denial never authorize a workspace",
    async () => {
      for (const options of [{ wrongUser: true }, { gatewayDenied: true }]) {
        const f = fixture(options);
        const flow = await f.start();
        const r = await f.handler(
          f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
        );
        eq(r.headers.get("location"), "/signin-error");
        eq(
          r.headers.getSetCookie().some((x) => x.startsWith("godmode_local=")),
          false,
        );
        if (options.wrongUser) eq(f.calls.read, 0);
      }
    },
  );
  Deno.test(
    provider +
      ": in-flight callback cannot complete after logout or race a duplicate exchange",
    async () => {
      let release!: () => void;
      const f = fixture({ wait: new Promise<void>((r) => release = r) });
      const flow = await f.start();
      const pending = f.handler(
        f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
      );
      await Promise.resolve();
      await f.handler(
        f.req(flow.callback + "&code=" + code, "GET", flow.cookie),
      );
      await f.handler(f.req("/logout", "POST", flow.cookie));
      release();
      eq((await pending).headers.get("location"), "/signin-error");
      eq(f.calls.exchange, 1);
      eq(f.calls.read, 0);
    },
  );
  Deno.test(
    provider +
      ": unsafe provider redirects are rejected and new starts are throttled",
    async () => {
      const bad = fixture({ unsafeRedirect: true });
      eq((await bad.handler(bad.req("/oauth/start", "POST"))).status, 503);
      eq(bad.calls.dispose, 1);
      const f = fixture();
      await f.start();
      eq((await f.handler(f.req("/oauth/start", "POST"))).status, 429);
      eq(f.calls.start, 1);
    },
  );
  Deno.test(
    provider +
      " SDK: isolated S256 verifiers and confirmed-user lookup with no provider token retention",
    async () => {
      const challenges = new Map<string, string>();
      let users = 0;
      const request: typeof fetch = async (input, init) => {
        const url = new URL(String(input));
        eq(url.origin, project);
        if (url.pathname.endsWith("/token")) {
          eq(url.searchParams.get("grant_type"), "pkce");
          const body = JSON.parse(String(init?.body));
          const digest = new Uint8Array(
            await crypto.subtle.digest(
              "SHA-256",
              new TextEncoder().encode(body.code_verifier),
            ),
          );
          const challenge = btoa(String.fromCharCode(...digest)).replaceAll(
            "+",
            "-",
          ).replaceAll("/", "_").replace(/=+$/, "");
          eq(challenge, challenges.get(body.auth_code));
          return Response.json({
            access_token: access,
            refresh_token: refresh,
            token_type: "bearer",
            expires_in: 3600,
            provider_token: "DO_NOT_RETAIN_MICROSOFT_TOKEN",
            user: { id: operator, email_confirmed_at: "2026-09-22T00:00:00Z" },
          });
        }
        eq(url.pathname, "/auth/v1/user");
        users++;
        return Response.json({
          id: operator,
          email_confirmed_at: "2026-09-22T00:00:00Z",
          is_anonymous: false,
        });
      };
      const begin = beginLogin("sb_publishable_fixture", request);
      const one = await begin(origin + "/oauth/callback?flow=one");
      const two = await begin(origin + "/oauth/callback?flow=two");
      for (const [id, flow] of [["one", one], ["two", two]] as const) {
        const url = new URL(flow.url);
        eq(url.searchParams.get("provider"), provider);
        eq(
          url.searchParams.get("scopes"),
          provider === "github" ? "user:email" : "email",
        );
        challenges.set(id, url.searchParams.get("code_challenge")!);
      }
      eq(challenges.get("one") === challenges.get("two"), false);
      const first = await one.exchange("one");
      const second = await two.exchange("two");
      eq(first.userId, operator);
      eq(second.userId, operator);
      eq(users, 2);
      eq(JSON.stringify(first).includes("DO_NOT_RETAIN"), false);
      one.dispose();
      two.dispose();
    },
  );
}
