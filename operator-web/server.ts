import { createClient } from "https://esm.sh/@supabase/supabase-js@2.57.4";
import { buildWeb } from "./handler.ts";
import { microsoftLogin } from "./microsoft.ts";

const project = "https://bnsmnztxkqmphvbikaxh.supabase.co";
function required(name: string) {
  const value = Deno.env.get(name);
  if (!value) throw Error(`Missing configuration: ${name}`);
  return value;
}
const origin = Deno.env.get("GOD_MODE_WEB_ORIGIN") ?? "http://127.0.0.1:4317";
const url = new URL(origin);
// Initial runnable release is local-only. A hosted deployment needs a reviewed
// reverse proxy/TLS configuration and durable session-store design.
if (url.hostname !== "127.0.0.1" || url.protocol !== "http:" || !url.port) {
  throw Error("This entrypoint requires an explicit 127.0.0.1 HTTP port");
}
const email = required("GOD_MODE_OPERATOR_EMAIL");
const operatorId = required("GOD_MODE_OPERATOR_USER_ID");
const key = required("SUPABASE_PUBLISHABLE_KEY");
if (!key.startsWith("sb_publishable_")) {
  throw Error(
    "A publishable key is required; never configure a service key here",
  );
}
const boundedFetch: typeof fetch = (input, init) =>
  fetch(input, {
    ...init,
    redirect: "error",
    signal: AbortSignal.timeout(20000),
  });
const auth = () =>
  createClient(project, key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    global: { fetch: boundedFetch },
  });
const handler = buildWeb({
  origin,
  operatorId,
  emailEnabled: Deno.env.get("GOD_MODE_LOGIN_EMAIL_ENABLED") === "true",
  microsoft: Deno.env.get("GOD_MODE_MICROSOFT_LOGIN_ENABLED") === "true"
    ? microsoftLogin(key, boundedFetch)
    : undefined,
  sendCode: async () => {
    const { error } = await auth().auth.signInWithOtp({
      email,
      options: { shouldCreateUser: false },
    });
    if (error) throw Error("Code request failed");
  },
  verifyCode: async (token) => {
    const { data, error } = await auth().auth.verifyOtp({
      email,
      token,
      type: "email",
    });
    if (error || !data.session || !data.user?.email_confirmed_at) {
      throw Error("Code verification failed");
    }
    return {
      ...data.session,
      expires_at: data.session.expires_at ?? 0,
      userId: data.user.id,
    };
  },
  refresh: async (refresh_token) => {
    const { data, error } = await auth().auth.refreshSession({ refresh_token });
    if (error || !data.session || !data.user?.email_confirmed_at) {
      throw Error("Refresh failed");
    }
    return {
      ...data.session,
      expires_at: data.session.expires_at ?? 0,
      userId: data.user.id,
    };
  },
  logout: async (token) => {
    const response = await boundedFetch(
      `${project}/auth/v1/logout?scope=local`,
      {
        method: "POST",
        headers: { apikey: key, authorization: `Bearer ${token}` },
      },
    );
    await response.body?.cancel();
    if (!response.ok) throw Error("Remote logout not confirmed");
  },
  read: (token, view) =>
    boundedFetch(
      `${project}/functions/v1/god-mode-operator-read?view=${view}`,
      {
        method: "GET",
        headers: {
          apikey: key,
          authorization: `Bearer ${token}`,
          accept: "application/json",
        },
      },
    ),
});
Deno.serve({ hostname: "127.0.0.1", port: Number(url.port) }, handler);
