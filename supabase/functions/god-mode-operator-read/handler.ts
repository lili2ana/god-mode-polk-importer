import { type Dependencies, json } from "../_shared/auth.ts";

const PROJECT_URL = "https://bnsmnztxkqmphvbikaxh.supabase.co";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;

// READY only: deployment needs an approved Auth user UUID and a verified login UI.
// Machine credentials are used only on the server, never accepted as operator login.
export function buildHandler(deps: Dependencies) {
  return async (req: Request): Promise<Response> => {
    try {
      const token = /^Bearer ([^\s]+)$/i.exec(
        req.headers.get("authorization") ?? "",
      )?.[1];
      if (!token || token.length > 8192 || token.startsWith("sb_")) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      if (req.method !== "GET") {
        return json({ ok: false, error: "Method not allowed" }, 405);
      }
      const requested = new URL(req.url);
      const view = requested.searchParams.get("view") ?? "dashboard";
      const entity = requested.searchParams.get("entity") ?? "all";
      if (
        !["dashboard", "crm"].includes(view) ||
        !["all", "seller", "buyer"].includes(entity) ||
        [...requested.searchParams.keys()].some((k) =>
          !["view", "entity"].includes(k)
        )
      ) {
        return json({ ok: false, error: "Invalid read request" }, 400);
      }
      const operators = (deps.env("GOD_MODE_OPERATOR_USER_IDS") ?? "").split(
        ",",
      ).map((x) => x.trim()).filter(Boolean);
      const url = deps.env("SUPABASE_URL");
      const keys = JSON.parse(deps.env("SUPABASE_SECRET_KEYS") ?? "{}");
      const legacy = deps.env("SUPABASE_SERVICE_ROLE_KEY");
      const secret = keys.default || legacy;
      if (
        !operators.length || operators.some((id) => !UUID.test(id)) ||
        url !== PROJECT_URL || !secret
      ) {
        return json({ ok: false, error: "Operator access unavailable" }, 503);
      }
      if (token === secret || token === legacy) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      const fetcher = deps.fetch ?? fetch;
      const auth = deps.createClient(url, secret, {
        auth: { persistSession: false, autoRefreshToken: false },
        global: {
          fetch: (input: RequestInfo | URL, init?: RequestInit) =>
            fetcher(input, {
              ...init,
              signal: AbortSignal.timeout(8000),
              redirect: "error",
            }),
        },
      });
      // getUser contacts Auth and verifies this access token. Never trust client
      // session objects, decoded claims, email strings or editable user_metadata.
      const { data, error } = await auth.auth.getUser(token);
      if (error || !data?.user) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      const user = data.user;
      if (user.is_anonymous !== false || !operators.includes(user.id)) {
        return json({ ok: false, error: "Forbidden" }, 403);
      }
      const route = view === "dashboard"
        ? "god-mode-dashboard?format=json"
        : `god-mode-crm-feed?entity=${entity}`;
      const headers: Record<string, string> = { accept: "application/json" };
      if (keys.default?.startsWith("sb_secret_")) headers.apikey = keys.default;
      else if (legacy) headers.authorization = `Bearer ${legacy}`;
      else return json({ ok: false, error: "Read service unavailable" }, 503);
      const reply = await fetcher(`${PROJECT_URL}/functions/v1/${route}`, {
        method: "GET",
        headers,
        redirect: "error",
        signal: AbortSignal.timeout(20000),
      });
      if (
        !reply.ok ||
        !reply.headers.get("content-type")?.includes("application/json")
      ) {
        await reply.body?.cancel();
        return json({ ok: false, error: "Read service unavailable" }, 502);
      }
      const body = await reply.json();
      if (body?.ok !== true) {
        return json({ ok: false, error: "Read service unavailable" }, 502);
      }
      // Do not forward upstream headers (cookies, redirects, CORS) or arbitrary fields.
      if (view === "crm") {
        if (
          !Array.isArray(body.contacts) || body.contacts.length > 200 ||
          body.count !== body.contacts.length
        ) {
          return json({ ok: false, error: "Invalid CRM response" }, 502);
        }
        return json({
          ok: true,
          count: body.count,
          contacts: body.contacts,
          outreach_authorized: false,
        });
      }
      const names = [
        "properties",
        "residential",
        "land",
        "preliminary_leads",
        "hot_preliminary_leads",
      ];
      if (
        names.some((name) =>
          !Number.isSafeInteger(body.counts?.[name]) || body.counts[name] < 0
        )
      ) {
        return json({ ok: false, error: "Invalid inventory response" }, 502);
      }
      return json({
        ok: true,
        counts: Object.fromEntries(
          names.map((name) => [name, body.counts[name]]),
        ),
        acquisition_authorized: false,
        outreach_authorized: false,
      });
    } catch {
      return json({ ok: false, error: "Operator read unavailable" }, 503);
    }
  };
}
