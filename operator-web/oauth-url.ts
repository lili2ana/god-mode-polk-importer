// Fixed destinations and permissions for both workspace and enrollment flows.
export function safeAuthorize(
  raw: string,
  callback: string,
  provider: "github" | "azure",
) {
  try {
    const url = new URL(raw);
    const keys = [
      "provider",
      "redirect_to",
      "scopes",
      "code_challenge",
      "code_challenge_method",
    ];
    return url.origin === "https://bnsmnztxkqmphvbikaxh.supabase.co" &&
      url.pathname === "/auth/v1/authorize" && !url.username && !url.password &&
      !url.hash &&
      [...url.searchParams.keys()].every((k) => keys.includes(k)) &&
      keys.every((k) => url.searchParams.getAll(k).length === 1) &&
      url.searchParams.get("provider") === provider &&
      url.searchParams.get("redirect_to") === callback &&
      url.searchParams.get("scopes") ===
        (provider === "github" ? "user:email" : "email") &&
      url.searchParams.get("code_challenge_method")?.toLowerCase() === "s256" &&
      /^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get("code_challenge") ?? "");
  } catch {
    return false;
  }
}
