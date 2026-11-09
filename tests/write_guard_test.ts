import { guardedWrite, guardedOutreach, WriteAuthorizationDenied } from "../supabase/functions/_shared/write_guard.ts";
const values: Array<string | undefined> = [undefined, "", "false", "FALSE", "0", "1", "True", "TRUE", "true ", " true", "yes", "null"];
function assert(condition: boolean, message: string): void { if (!condition) throw new Error(message); }
for (const flag of values) {
  Deno.test("database request never issued with ALLOW_DB_WRITES=" + JSON.stringify(flag), async () => {
    const client = { from(_table: string) { throw new Error("UNAUTHORIZED_DB_QUERY_EXECUTED"); } };
    let denied = false;
    try {
      await guardedWrite(async () => client.from("outreach_events"), n => n === "ALLOW_DB_WRITES" ? flag : "true");
    } catch (error) {
      denied = error instanceof WriteAuthorizationDenied && error.code === "DB_WRITES_DISABLED";
    }
    assert(denied, "Expected WriteAuthorizationDenied before Supabase query");
  });
  Deno.test("outreach callback never issued with ALLOW_DB_WRITES=" + JSON.stringify(flag), async () => {
    let calls = 0;
    let denied = false;
    try {
      await guardedOutreach(async () => { calls++; }, n => n === "ALLOW_DB_WRITES" ? flag : "true");
    } catch (error) {
      denied = error instanceof WriteAuthorizationDenied;
    }
    assert(denied && calls === 0, "Expected denial before outbound callback");
  });
}
Deno.test("outreach requires its own flag", async () => {
  let calls = 0, denied = false;
  try { await guardedOutreach(async () => { calls++; }, n => n === "ALLOW_DB_WRITES" ? "true" : undefined); }
  catch (error) { denied = error instanceof WriteAuthorizationDenied && error.code === "OUTREACH_DISABLED"; }
  assert(denied && calls === 0, "Outreach must remain disabled");
});
