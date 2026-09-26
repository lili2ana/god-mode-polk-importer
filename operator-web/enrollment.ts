import type { WebDependencies } from "./handler.ts";
import { safeAuthorize } from "./oauth-url.ts";

export type EnrollmentObservation = {
  userId: string;
  observedAt: string;
  remoteLogoutConfirmed: true;
  authorizationGranted: false;
};
type Dependencies = {
  origin: string;
  start: NonNullable<WebDependencies["github"]>;
  revoke: (token: string) => Promise<void>;
  record: (observation: EnrollmentObservation) => Promise<void>;
  now?: () => number;
};

// Enrollment only observes a confirmed Auth UUID. It cannot read business data,
// issue workspace sessions, authorize a UUID, or write an operator allowlist.
export function buildEnrollment(deps: Dependencies) {
  const origin = new URL(deps.origin);
  if (
    origin.origin !== deps.origin || origin.hostname !== "127.0.0.1" ||
    origin.protocol !== "http:" || !origin.port
  ) throw Error("Loopback origin required");
  const now = deps.now ?? Date.now;
  type Flow = Awaited<ReturnType<Dependencies["start"]>> & {
    id: string;
    expires: number;
    busy: boolean;
  };
  let flow: Flow | undefined, lastStart = -Infinity, completed = false;
  const clear = () => {
    flow?.dispose();
    flow = undefined;
  };
  const cookieName = "godmode_enrollment";
  const cookie = (id = "", age = 0) =>
    `${cookieName}=${id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${age}`;
  const page = (
    body: string,
    status = 200,
    headers: Record<string, string> = {},
  ) =>
    new Response(
      `<!doctype html><html lang="en"><meta charset="utf-8"><title>God Mode account setup</title><h1>God Mode account setup</h1>${body}</html>`,
      {
        status,
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store",
          "content-security-policy":
            "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
          "referrer-policy": "no-referrer",
          "x-content-type-options": "nosniff",
          ...headers,
        },
      },
    );
  const fail = () =>
    page(
      "<p>Setup could not be verified. No workspace access was granted.</p>",
      303,
      { location: "/failed", "set-cookie": cookie() },
    );
  const escape = (text: string) =>
    text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  return async (req: Request): Promise<Response> => {
    try {
      const url = new URL(req.url);
      if (
        url.origin !== deps.origin ||
        (req.headers.has("host") && req.headers.get("host") !== origin.host) ||
        (url.search && url.pathname !== "/enroll/callback")
      ) return page("<p>Invalid request.</p>", 400);
      if (!["GET", "POST"].includes(req.method)) {
        return page("<p>Method not allowed.</p>", 405);
      }
      if (req.method === "POST" && req.headers.get("origin") !== deps.origin) {
        return page("<p>Request denied.</p>", 403);
      }
      if (flow && flow.expires <= now()) clear();
      if (req.method === "GET" && url.pathname === "/") {
        return page(
          completed
            ? "<p>Account observation recorded for review. Workspace access has not been granted. You can close this page.</p>"
            : '<p>Sign in to identify the account you want to use. This may create an authentication account, but grants no access to property or CRM records.</p><form method="post" action="/enroll/start"><button>Identify my GitHub account</button></form>',
          200,
          // no-referrer makes native form POSTs send Origin: null.
          // Keep the exact-origin check and disclose no cross-origin referrer.
          completed ? {} : { "referrer-policy": "same-origin" },
        );
      }
      if (req.method === "GET" && url.pathname === "/failed") {
        return page(
          '<p>Setup could not be verified. No workspace access was granted. Remote sign-out may require review.</p><a href="/">Return</a>',
          403,
        );
      }
      if (req.method === "POST" && url.pathname === "/enroll/cancel") {
        clear();
        return page("<p>Setup cancelled. No access granted.</p>", 200, {
          "set-cookie": cookie(),
        });
      }
      if (req.method === "POST" && url.pathname === "/enroll/start") {
        if (completed) return page("<p>Observation already recorded.</p>", 409);
        if (flow || now() - lastStart < 10000) {
          return page(
            "<p>A setup attempt is already pending. Finish it or wait five minutes.</p>",
            429,
          );
        }
        lastStart = now();
        const id = crypto.randomUUID() + crypto.randomUUID();
        // Reserve before awaiting the provider to prevent parallel starts/cancellation races.
        const reservation = {
          id,
          expires: now() + 300000,
          busy: true,
          url: "",
          exchange: async () => {
            throw Error();
          },
          dispose: () => {},
        };
        flow = reservation;
        const callback = `${deps.origin}/enroll/callback?flow=${id}`;
        let started: Awaited<ReturnType<Dependencies["start"]>>;
        try {
          started = await deps.start(callback);
        } catch {
          if (flow === reservation) clear();
          return fail();
        }
        if (
          flow !== reservation || reservation.expires <= now() ||
          !safeAuthorize(started.url, callback, "github")
        ) {
          started.dispose();
          if (flow === reservation) clear();
          return fail();
        }
        flow = { ...started, id, expires: reservation.expires, busy: false };
        return page(
          `<p><a href="${escape(started.url)}">Continue with GitHub</a></p>`,
          200,
          { "set-cookie": cookie(id, 300) },
        );
      }
      if (req.method === "GET" && url.pathname === "/enroll/callback") {
        const current = flow, params = url.searchParams;
        const cookies = (req.headers.get("cookie") ?? "").split(";").map((x) =>
          x.trim()
        ).filter((x) => x.startsWith(cookieName + "="));
        if (
          !current || current.busy || cookies.length !== 1 ||
          cookies[0] !== `${cookieName}=${current.id}` ||
          params.getAll("flow").length !== 1 ||
          params.get("flow") !== current.id
        ) return fail();
        current.busy = true;
        let tokens: Awaited<ReturnType<Flow["exchange"]>> | undefined;
        let revoked = false;
        try {
          const code = params.get("code") ?? "";
          if (
            params.getAll("code").length !== 1 ||
            !/^[A-Za-z0-9_-]{16,2048}$/.test(code) ||
            [...params.keys()].some((k) => !["code", "flow"].includes(k))
          ) return fail();
          tokens = await current.exchange(code);
          if (
            !/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(
              tokens.userId,
            ) ||
            !tokens.access_token || !tokens.refresh_token ||
            !Number.isFinite(tokens.expires_at) ||
            tokens.expires_at * 1000 <= now() || flow !== current ||
            current.expires <= now()
          ) return fail();
          await deps.revoke(tokens.access_token);
          revoked = true;
          if (flow !== current || current.expires <= now()) return fail();
          await deps.record({
            userId: tokens.userId,
            observedAt: new Date(now()).toISOString(),
            remoteLogoutConfirmed: true,
            authorizationGranted: false,
          });
          completed = true;
          return page("<p>Continue.</p>", 303, {
            location: "/",
            "set-cookie": cookie(),
          });
        } finally {
          if (tokens?.access_token && !revoked) {
            try {
              await deps.revoke(tokens.access_token);
            } catch { /* Never claim remote success. */ }
          }
          if (flow === current) clear();
          else current.dispose();
        }
      }
      return page("<p>Page not found.</p>", 404);
    } catch {
      return fail();
    }
  };
}
