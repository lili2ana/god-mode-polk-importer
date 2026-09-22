import { createClient } from "https://esm.sh/@supabase/supabase-js@2.57.4";
import type { WebDependencies } from "./handler.ts";

const project = "https://bnsmnztxkqmphvbikaxh.supabase.co";

// Each browser login gets an isolated server-memory PKCE store. No verifier,
// OAuth provider token, or refresh token is put in a cookie or redirect URL.
export function oauthLogin(
  provider: "azure" | "github",
  key: string,
  boundedFetch: typeof fetch,
): NonNullable<WebDependencies["microsoft"]> {
  if (!["azure", "github"].includes(provider)) {
    throw Error("Unsupported login provider");
  }
  if (!key.startsWith("sb_publishable_")) {
    throw Error("Publishable key required");
  }
  return async (callback) => {
    const memory = new Map<string, string>();
    const client = createClient(project, key, {
      auth: {
        flowType: "pkce",
        persistSession: true,
        autoRefreshToken: false,
        detectSessionInUrl: false,
        storage: {
          getItem: (name) => memory.get(name) ?? null,
          setItem: (name, value) => {
            memory.set(name, value);
          },
          removeItem: (name) => {
            memory.delete(name);
          },
        },
      },
      global: { fetch: boundedFetch },
    });
    try {
      const { data, error } = await client.auth.signInWithOAuth({
        provider,
        options: {
          scopes: provider === "github" ? "user:email" : "email",
          redirectTo: callback,
          skipBrowserRedirect: true,
        },
      });
      if (error || !data.url) throw Error("Provider sign-in unavailable");
      return {
        url: data.url,
        dispose: () => memory.clear(),
        exchange: async (code: string) => {
          const { data, error } = await client.auth.exchangeCodeForSession(
            code,
          );
          if (error || !data.session) {
            throw Error("Provider code exchange failed");
          }
          const user = await client.auth.getUser(data.session.access_token);
          if (
            user.error || !user.data.user?.email_confirmed_at ||
            user.data.user.is_anonymous
          ) {
            throw Error("Verified provider identity required");
          }
          return {
            access_token: data.session.access_token,
            refresh_token: data.session.refresh_token,
            expires_at: data.session.expires_at ?? 0,
            userId: user.data.user.id,
          };
        },
      };
    } catch {
      memory.clear();
      throw Error("Provider sign-in unavailable");
    }
  };
}
