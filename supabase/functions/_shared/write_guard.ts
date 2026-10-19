/** Fail-closed authorization for server-side database mutations and outbound outreach. */
export class WriteAuthorizationDenied extends Error {
  constructor(public readonly code = "DB_WRITES_DISABLED") {
    super(code);
    this.name = "WriteAuthorizationDenied";
  }
}
export function assertDbWritesAuthorized(getEnv: (name: string) => string | undefined = (name) => Deno.env.get(name)): void {
  if (getEnv("ALLOW_DB_WRITES") !== "true") throw new WriteAuthorizationDenied();
}
export function assertOutreachAuthorized(getEnv: (name: string) => string | undefined = (name) => Deno.env.get(name)): void {
  assertDbWritesAuthorized(getEnv);
  if (getEnv("ALLOW_SELLER_OUTREACH") !== "true") throw new WriteAuthorizationDenied("OUTREACH_DISABLED");
}
export async function guardedWrite<T>(operation: () => Promise<T>, getEnv: (name: string) => string | undefined): Promise<T> {
  assertDbWritesAuthorized(getEnv);
  return await operation();
}
export async function guardedOutreach<T>(operation: () => Promise<T>, getEnv: (name: string) => string | undefined): Promise<T> {
  assertOutreachAuthorized(getEnv);
  return await operation();
}
