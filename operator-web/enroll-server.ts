import { buildEnrollment } from "./enrollment.ts";
import { oauthLogin } from "./oauth.ts";

if (Deno.env.get("GOD_MODE_ENROLLMENT_ENABLED") !== "true") {
  throw Error("Enrollment is disabled");
}
const key = Deno.env.get("SUPABASE_PUBLISHABLE_KEY") ?? "";
if (!key.startsWith("sb_publishable_")) throw Error("Publishable key required");
const origin = "http://127.0.0.1:4319";
// Run from the repository root. The existing ignored directory is mandatory.
const path = ".polk_import/operator-enrollment-observation.json";
if (!(await Deno.stat(".polk_import")).isDirectory) {
  throw Error("Private evidence directory required");
}
try {
  await Deno.stat(path);
  throw Error("Existing observation must be reviewed first");
} catch (error) {
  if (!(error instanceof Deno.errors.NotFound)) throw error;
}
const request: typeof fetch = (input, init) =>
  fetch(input, {
    ...init,
    redirect: "error",
    signal: AbortSignal.timeout(20000),
  });
const handler = buildEnrollment({
  origin,
  start: oauthLogin("github", key, request),
  revoke: async (token) => {
    const response = await request(
      "https://bnsmnztxkqmphvbikaxh.supabase.co/auth/v1/logout?scope=local",
      {
        method: "POST",
        headers: { apikey: key, authorization: `Bearer ${token}` },
      },
    );
    await response.body?.cancel();
    if (!response.ok) throw Error("Remote revocation unconfirmed");
  },
  record: async (observation) => {
    // Never persist access/refresh/provider tokens or automatically authorize.
    const file = await Deno.open(path, {
      write: true,
      createNew: true,
      mode: 0o600,
    });
    try {
      const bytes = new TextEncoder().encode(
        JSON.stringify(observation, null, 2) + "\n",
      );
      let offset = 0;
      while (offset < bytes.length) {
        const written = await file.write(bytes.subarray(offset));
        if (written <= 0) throw Error("Evidence write incomplete");
        offset += written;
      }
      await file.sync();
    } finally {
      file.close();
    }
  },
});
Deno.serve({ hostname: "127.0.0.1", port: 4319 }, handler);
