import { buildHandler as buildDdWorker } from "../supabase/functions/god-mode-dd-worker/handler.ts";
import { buildHandler as buildTitleWorker } from "../supabase/functions/god-mode-title-access-worker/handler.ts";
import { buildHandler } from "../supabase/functions/god-mode-dd-finalize/handler.ts";
import { handleRequest } from "../supabase/functions/god-mode-gis-verify/index.ts";
function assert(v:unknown,msg:string){if(!v)throw Error(msg);}
for(const flag of [undefined,"","false","FALSE","0","true ","yes"]){
 Deno.test("dd-finalize HTTP 403 without DB: "+JSON.stringify(flag),async()=>{
  let calls=0;
  const handler=buildHandler({env:(n:string)=>n==="ALLOW_DB_WRITES"?flag:undefined,createClient:()=>{calls++;throw Error("DB_CREATED");}});
  const res=await handler(new Request("http://localhost/",{method:"POST"}));
  assert(res.status===403,"expected 403");
  assert((await res.json()).error==="DB_WRITES_DISABLED","expected denial code");
  assert(calls===0,"client initialized");
 });
 Deno.test("gis-verify HTTP 403 without DB: "+JSON.stringify(flag),async()=>{
  const res=await handleRequest(new Request("http://localhost/",{method:"POST"}),(n:string)=>n==="ALLOW_DB_WRITES"?flag:undefined);
  assert(res.status===403,"expected 403");
  assert((await res.json()).error==="DB_WRITES_DISABLED","expected denial code");
 });
}

for (const [name, build] of [["dd-worker", buildDdWorker], ["title-access-worker", buildTitleWorker]] as const) {
  for (const flag of [undefined, "", "false", "FALSE", "0", "true ", "yes"]) {
    Deno.test(name + " rejects disabled writes without client or network: " + JSON.stringify(flag), async () => {
      let clients = 0, requests = 0;
      const handler = build({
        env: (n: string) => n === "ALLOW_DB_WRITES" ? flag : undefined,
        createClient: () => { clients++; throw Error("DB_CLIENT_CREATED"); },
        fetch: (async () => { requests++; throw Error("NETWORK_CALLED"); }) as typeof fetch,
      });
      const response = await handler(new Request("http://localhost/", { method: "POST" }));
      assert(response.status === 403, "expected HTTP 403");
      assert((await response.json()).error === "DB_WRITES_DISABLED", "expected write denial");
      assert(clients === 0 && requests === 0, "unexpected database or network activity");
    });
  }
}
