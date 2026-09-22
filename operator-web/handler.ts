// Single-process private web interface. No Auth tokens are stored in the browser.
type Tokens = {
  access_token: string;
  refresh_token: string;
  expires_at: number;
};
type Session = Tokens & { deadline: number; refresh?: Promise<void> };
export type WebDependencies = {
  origin: string;
  operatorId: string;
  emailEnabled: boolean;
  sendCode: () => Promise<void>;
  verifyCode: (code: string) => Promise<Tokens & { userId: string }>;
  refresh: (token: string) => Promise<Tokens & { userId: string }>;
  logout: (token: string) => Promise<void>;
  read: (token: string, view: "dashboard" | "crm") => Promise<Response>;
  now?: () => number;
};
const escape = (v: unknown) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]!),
  );
const frame = (body: string) =>
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>God Mode · Private workspace</title><style>body{background:#101923;color:#e8eef4;font:17px system-ui;max-width:900px;margin:5vh auto;padding:24px}header{color:#91adbd;letter-spacing:.1em}h1{font-size:36px}section{background:#192837;border:1px solid #304555;border-radius:16px;padding:24px;margin:24px 0}button,a{color:#a4e8d2}button{background:#24574a;border:1px solid #69baa0;border-radius:8px;padding:12px 20px;font:inherit;cursor:pointer}input{font:inherit;padding:12px;border-radius:8px;max-width:180px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px;border-bottom:1px solid #304555}nav{display:flex;gap:24px}p{line-height:1.6}.muted{color:#a5b9c9}</style></head><body><header>GOD MODE / PRIVATE WORKSPACE</header>${body}<p class="muted">Seller outreach is off. Preliminary signals are not verified valuations or acquisition approval.</p></body></html>`;
const login =
  `<h1>Your private deal workspace</h1><section><h2>Sign in</h2><p>Send a one-time code to your approved email address. Enter it here to open your dashboard and CRM.</p><form method="post" action="/request-code"><button>Send my sign-in code</button></form></section>`;
const verify =
  `<h1>Check your email</h1><section><p>Enter the six-digit sign-in code. Never share it in chat.</p><form method="post" action="/verify-code"><label for="code">Sign-in code</label> <input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required><button>Open workspace</button></form></section>`;

export function buildWeb(deps: WebDependencies) {
  const origin = new URL(deps.origin);
  if (
    origin.origin !== deps.origin || (origin.protocol !== "https:" &&
      !(origin.protocol === "http:" &&
        ["127.0.0.1", "localhost"].includes(origin.hostname)))
  ) {
    throw Error("HTTPS or loopback origin required");
  }
  if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(deps.operatorId)) {
    throw Error("Approved operator UUID required");
  }
  const now = deps.now ?? Date.now;
  const sessions = new Map<string, Session>();
  const cookieName = origin.protocol === "https:"
    ? "__Host-godmode"
    : "godmode_local";
  const cookie = (id: string, maxAge: number) =>
    `${cookieName}=${id}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${
      origin.protocol === "https:" ? "; Secure" : ""
    }`;
  const page = (
    body: string,
    status = 200,
    extra: Record<string, string> = {},
  ) =>
    new Response(frame(body), {
      status,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "content-security-policy":
          "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        "referrer-policy": "no-referrer",
        "x-content-type-options": "nosniff",
        ...extra,
      },
    });
  const redirect = (id: string, age: number) =>
    page("<p>Continue to your workspace.</p>", 303, {
      location: "/",
      "set-cookie": cookie(id, age),
    });
  let lastSend = -Infinity, attemptWindow = 0, attempts = 0;
  function validTokens(value: Tokens & { userId: string }) {
    return value.userId === deps.operatorId &&
      typeof value.access_token === "string" &&
      value.access_token.length > 0 &&
      typeof value.refresh_token === "string" &&
      value.refresh_token.length > 0 && Number.isFinite(value.expires_at) &&
      value.expires_at * 1000 > now();
  }
  async function ready(id: string, session: Session) {
    if (session.expires_at * 1000 <= now() + 60000) {
      if (!session.refresh) {
        session.refresh = (async () => {
          const tokens = await deps.refresh(session.refresh_token);
          if (
            !validTokens(tokens) || sessions.get(id) !== session
          ) throw Error("Session no longer valid");
          Object.assign(session, {
            access_token: tokens.access_token,
            refresh_token: tokens.refresh_token,
            expires_at: tokens.expires_at,
          });
        })();
      }
      try {
        await session.refresh;
      } finally {
        delete session.refresh;
      }
    }
    if (sessions.get(id) !== session) throw Error("Signed out");
  }
  return async (req: Request) => {
    try {
      const url = new URL(req.url);
      if (
        url.origin !== deps.origin || url.search ||
        req.headers.get("host") && req.headers.get("host") !== origin.host
      ) {
        return page("<h1>Invalid request</h1>", 400);
      }
      if (!["GET", "POST"].includes(req.method)) {
        return page("<h1>Method not allowed</h1>", 405);
      }
      if (req.method === "POST" && req.headers.get("origin") !== deps.origin) {
        return page("<h1>Request denied</h1>", 403);
      }
      for (const [key, session] of sessions) {
        if (session.deadline <= now()) sessions.delete(key);
      }
      const matches = (req.headers.get("cookie") ?? "").split(";").map((x) =>
        x.trim()
      ).filter((x) => x.startsWith(cookieName + "="));
      const id = matches.length === 1
        ? matches[0].slice(cookieName.length + 1)
        : "";
      const session = sessions.get(id);
      if (req.method === "POST" && url.pathname === "/request-code") {
        if (!deps.emailEnabled) {
          return page(
            "<h1>Sign-in is being prepared</h1><p>Email delivery has not been enabled yet. No email was sent.</p>",
            503,
          );
        }
        if (now() - lastSend < 60000) {
          return page(
            "<h1>Please wait a minute before requesting another code.</h1>",
            429,
          );
        }
        lastSend = now();
        await deps.sendCode();
        return page(verify);
      }
      if (req.method === "POST" && url.pathname === "/verify-code") {
        if (!deps.emailEnabled) {
          return page("<h1>Sign-in is being prepared</h1>", 503);
        }
        if (now() - attemptWindow >= 60000) {
          attempts = 0;
          attemptWindow = now();
        }
        if (++attempts > 5) {
          return page(
            "<h1>Please wait a minute before trying again.</h1>",
            429,
          );
        }
        if (
          !req.headers.get("content-type")?.startsWith(
            "application/x-www-form-urlencoded",
          )
        ) return page("<h1>Invalid form</h1>", 400);
        // Stream cap also protects requests that omit or forge Content-Length.
        const reader = req.body?.getReader();
        let body = "", size = 0;
        if (reader) {
          const decoder = new TextDecoder();
          while (true) {
            const chunk = await reader.read();
            if (chunk.done) break;
            size += chunk.value.length;
            if (size > 256) {
              await reader.cancel();
              return page("<h1>Invalid form</h1>", 413);
            }
            body += decoder.decode(chunk.value, { stream: true });
          }
          body += decoder.decode();
        }
        const form = new URLSearchParams(body), code = form.get("code") ?? "";
        if (
          form.getAll("code").length !== 1 || [...form.keys()].some((k) =>
            k !== "code"
          ) || !/^\d{6}$/.test(code)
        ) return page("<h1>Enter a six-digit code.</h1>", 400);
        const tokens = await deps.verifyCode(code);
        if (!validTokens(tokens)) {
          return page("<h1>Sign-in could not be verified.</h1>", 403);
        }
        // Prove the deployed read gateway accepts this operator before issuing a cookie.
        const gate = await deps.read(tokens.access_token, "dashboard");
        if (!gate.ok || (await gate.json())?.ok !== true) {
          return page("<h1>Workspace access is not ready.</h1>", 403);
        }
        if (sessions.size >= 50) {
          return page("<h1>Sign-in temporarily unavailable.</h1>", 503);
        }
        if (session) sessions.delete(id);
        const fresh = crypto.randomUUID() + crypto.randomUUID();
        sessions.set(fresh, { ...tokens, deadline: now() + 8 * 3600000 });
        return redirect(fresh, 8 * 3600);
      }
      if (req.method === "POST" && url.pathname === "/logout") {
        if (session) {
          sessions.delete(id); // Local access ends even if the remote call fails.
          try {
            if (session.refresh) await session.refresh.catch(() => {});
            await deps.logout(session.access_token);
          } catch {
            return page(
              "<h1>Signed out of this workspace</h1><p>Local access is closed. The authentication service could not confirm remote session revocation.</p>",
              503,
              { "set-cookie": cookie("", 0) },
            );
          }
        }
        return redirect("", 0);
      }
      if (req.method !== "GET" || !["/", "/crm"].includes(url.pathname)) {
        return page("<h1>Page not found</h1>", 404);
      }
      if (!session) return page(login);
      try {
        await ready(id, session);
      } catch {
        sessions.delete(id);
        return redirect("", 0);
      }
      const view = url.pathname === "/crm" ? "crm" : "dashboard";
      const response = await deps.read(session.access_token, view);
      if ([401, 403].includes(response.status)) {
        sessions.delete(id);
        return redirect("", 0);
      }
      if (!response.ok) {
        return page(
          "<h1>Workspace temporarily unavailable</h1><p>Please try again shortly.</p>",
          503,
        );
      }
      const data = await response.json();
      if (data?.ok !== true || sessions.get(id) !== session) {
        return page("<h1>Workspace unavailable</h1>", 503);
      }
      const nav =
        `<nav><a href="/">Dashboard</a><a href="/crm">CRM review</a></nav><form method="post" action="/logout"><button>Sign out</button></form>`;
      let content: string;
      if (view === "dashboard") {
        const labels: Record<string, string> = {
          properties: "Properties",
          residential: "Residential",
          land: "Land",
          preliminary_leads: "Preliminary leads",
          hot_preliminary_leads: "High-priority preliminary leads",
        };
        if (
          Object.keys(labels).some((k) =>
            !Number.isSafeInteger(data.counts?.[k]) || data.counts[k] < 0
          )
        ) throw Error("Invalid counts");
        content = Object.entries(labels).map(([k, label]) =>
          `<tr><th>${label}</th><td>${
            data.counts[k].toLocaleString("en-US")
          }</td></tr>`
        ).join("");
      } else {
        if (
          !Array.isArray(data.contacts) || data.contacts.length > 200 ||
          data.count !== data.contacts.length
        ) throw Error("Invalid contacts");
        content =
          `<tr><th>Contact</th><th>Type</th><th>Review status</th></tr>` +
          data.contacts.map((c: any) =>
            `<tr><td>${escape(c.name || "Name unavailable")}</td><td>${
              escape(c.entity_type)
            }</td><td>${escape(c.crm_status)}</td></tr>`
          ).join("");
        if (!data.contacts.length) {
          content += "<tr><td>No contacts ready for review.</td></tr>";
        }
      }
      return page(
        `<h1>${
          view === "crm" ? "CRM review" : "Your property workspace"
        }</h1>${nav}<section><table>${content}</table></section>`,
      );
    } catch {
      return page(
        "<h1>Unable to complete this request</h1><p>Please try again. No credentials or private error details are shown.</p>",
        503,
      );
    }
  };
}
