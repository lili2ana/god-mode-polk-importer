import { oauthLogin } from "./oauth.ts";

export function microsoftLogin(key: string, boundedFetch: typeof fetch) {
  return oauthLogin("azure", key, boundedFetch);
}
