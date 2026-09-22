// Internal service endpoints only. Never accept a public key or an arbitrary user JWT.
export type Dependencies = {
  env: (name: string) => string | undefined;
  createClient: (url: string, key: string, options?: any) => any;
  fetch?: typeof fetch;
};
export function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
}
export async function sha256(value: string) {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(
    new Uint8Array(bytes),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
}
async function equalSecret(a: string, b: string) {
  const x = await sha256(a), y = await sha256(b);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return diff === 0;
}
export function protect(
  scope: string,
  method: string,
  work: (req: Request, sb: any) => Promise<Response>,
  deps: Dependencies,
) {
  return async (req: Request): Promise<Response> => {
    try {
      const scoped = req.headers.get("x-god-mode-token");
      const apiKey = req.headers.get("apikey");
      const bearer = /^Bearer ([^\s]+)$/i.exec(
        req.headers.get("authorization") ?? "",
      )?.[1];
      if (!scoped && !apiKey && !bearer) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      let keys: Record<string, string> = {};
      try {
        keys = JSON.parse(deps.env("SUPABASE_SECRET_KEYS") || "{}");
      } catch {
        return json({ ok: false, error: "Service unavailable" }, 503);
      }
      const legacy = deps.env("SUPABASE_SERVICE_ROLE_KEY");
      const secret = keys.default || legacy;
      const url = deps.env("SUPABASE_URL");
      if (!secret || !url) {
        return json({ ok: false, error: "Service unavailable" }, 503);
      }
      let authorized = false;
      if (apiKey && keys.default?.startsWith("sb_secret_")) {
        authorized = await equalSecret(apiKey, keys.default);
      }
      if (!authorized && bearer && legacy) {
        authorized = await equalSecret(bearer, legacy);
      }
      let sb: any;
      if (!authorized && scoped) {
        if (!/^[a-f0-9]{64}$/.test(scoped)) {
          return json({ ok: false, error: "Unauthorized" }, 401);
        }
        sb = deps.createClient(url, secret, {
          auth: { persistSession: false, autoRefreshToken: false },
        });
        const { data, error } = await sb.rpc("god_mode_check_internal_token", {
          p_scope: scope,
          p_digest: await sha256(scoped),
        });
        if (error) {
          return json({ ok: false, error: "Authorization unavailable" }, 503);
        }
        authorized = data === true;
      }
      if (!authorized) return json({ ok: false, error: "Unauthorized" }, 401);
      if (req.method !== method) {
        return json({ ok: false, error: "Method not allowed" }, 405);
      }
      if (new URL(req.url).searchParams.get("check") === "auth") {
        return json({ ok: true, scope, authorized: true, side_effects: false });
      }
      sb ??= deps.createClient(url, secret, {
        auth: { persistSession: false, autoRefreshToken: false },
      });
      return await work(req, sb);
    } catch {
      return json({ ok: false, error: "Service unavailable" }, 503);
    }
  };
}
