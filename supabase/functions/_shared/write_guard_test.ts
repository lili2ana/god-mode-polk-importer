import { assertDbWritesAuthorized, assertOutreachAuthorized, guardedWrite, guardedOutreach, WriteAuthorizationDenied } from "./write_guard.ts";
function check(value: unknown, message: string) { if (!value) throw new Error(message); }
const denied = [undefined, "", "false", "FALSE", "True", "TRUE", "1", "yes", " true", "true ", "0"];
for (const flag of denied) {
  Deno.test("fail closed for " + JSON.stringify(flag), async () => {
    let writes = 0, messages = 0;
    const env = (name: string) => name === "ALLOW_DB_WRITES" ? flag : "true";
    let code = "";
    try { await guardedWrite(async () => { writes++; return 1; }, env); } catch (e) { check(e instanceof WriteAuthorizationDenied, "wrong exception"); code = (e as WriteAuthorizationDenied).code; }
    check(code === "DB_WRITES_DISABLED", "missing denial");
    try { await guardedOutreach(async () => { messages++; return 1; }, env); } catch (e) { check(e instanceof WriteAuthorizationDenied, "wrong exception"); }
    check(writes === 0 && messages === 0, "side effect occurred");
  });
}
Deno.test("outreach disabled independently", async () => {
  let calls = 0; let code = "";
  try { await guardedOutreach(async () => { calls++; }, n => n === "ALLOW_DB_WRITES" ? "true" : "false"); } catch(e) { code = (e as WriteAuthorizationDenied).code; }
  check(code === "OUTREACH_DISABLED" && calls === 0, "outreach was not blocked");
});
Deno.test("explicit authorization only", async () => {
  const env = (n: string) => n === "ALLOW_DB_WRITES" || n === "ALLOW_SELLER_OUTREACH" ? "true" : undefined;
  let writes = 0, messages = 0;
  await guardedWrite(async () => { writes++; }, env);
  await guardedOutreach(async () => { messages++; }, env);
  check(writes === 1 && messages === 1, "authorized callback not executed");
});
Deno.test("direct guard rejects unset flag", () => {
  let caught = false;
  try { assertDbWritesAuthorized(() => undefined); } catch(e) { caught = e instanceof WriteAuthorizationDenied; }
  check(caught, "direct guard allowed write");
});
Deno.test("outreach guard rejects unset outreach flag", () => {
  let caught = false;
  try { assertOutreachAuthorized(n => n === "ALLOW_DB_WRITES" ? "true" : undefined); } catch(e) { caught = e instanceof WriteAuthorizationDenied; }
  check(caught, "direct guard allowed outreach");
});
