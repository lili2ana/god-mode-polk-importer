import { buildHandler as worker } from "../god-mode-dd-worker/handler.ts";
import { buildHandler as finalize } from "../god-mode-dd-finalize/handler.ts";
import { buildHandler as crm } from "../god-mode-crm-feed/handler.ts";
import { amount, gis } from "../_shared/evidence.ts";
import { sha256 } from "../_shared/auth.ts";
function eq(a: unknown, b: unknown) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`Expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
}
const key = "sb_secret_test_backend_only",
  token = "a".repeat(64),
  parcel = "123456789012345678";
const env = (name: string) =>
  ({
    SUPABASE_URL: "https://example.invalid",
    SUPABASE_SECRET_KEYS: JSON.stringify({ default: key }),
    SUPABASE_SERVICE_ROLE_KEY: "server-legacy-test",
  } as Record<string, string>)[name];
const handlers = [["god-mode-dd-worker", worker, "POST"], [
  "god-mode-dd-finalize",
  finalize,
  "POST",
], ["god-mode-crm-feed", crm, "GET"]] as const;
for (const [scope, build, method] of handlers) {
  Deno.test(`${scope}: unauthenticated/public/forged requests cannot access database`, async () => {
    let calls = 0;
    const h = build({
      env,
      createClient: () => {
        calls++;
        throw Error("must not query");
      },
    });
    for (
      const headers of [{}, { apikey: "sb_publishable_public" }, {
        authorization: "Bearer forged.user.jwt",
      }, { "x-god-mode-token": "bad" }]
    ) {
      eq(
        (await h(
          new Request("https://example.invalid?check=auth", {
            method,
            headers: headers as unknown as Record<string, string>,
          }),
        )).status,
        401,
      );
    }
    eq(calls, 0);
  });
  Deno.test(`${scope}: service health check has no business side effects`, async () => {
    let calls = 0;
    const h = build({
      env,
      createClient: () => {
        calls++;
        throw Error("no business query");
      },
    });
    for (
      const headers of [{ apikey: key }, {
        authorization: "Bearer server-legacy-test",
      }]
    ) {
      const r = await h(
        new Request("https://example.invalid?check=auth", {
          method,
          headers: headers as unknown as Record<string, string>,
        }),
      );
      eq(r.status, 200);
      eq((await r.json()).side_effects, false);
    }
    eq(calls, 0);
  });
  Deno.test(`${scope}: scoped token checks exact scope and rejects mismatch`, async () => {
    let allowedScope: string = scope;
    let calls = 0;
    const h = build({
      env,
      createClient: () => ({
        rpc: async (name: string, args: any) => {
          calls++;
          eq(name, "god_mode_check_internal_token");
          eq(args.p_digest, await sha256(token));
          return { data: args.p_scope === allowedScope, error: null };
        },
        from: () => {
          throw Error("business query forbidden");
        },
      }),
    });
    const request = () =>
      new Request("https://example.invalid?check=auth", {
        method,
        headers: { "x-god-mode-token": token },
      });
    eq((await h(request())).status, 200);
    allowedScope = "other-worker";
    eq((await h(request())).status, 401);
    eq(calls, 2);
  });
  Deno.test(`${scope}: auth failure and wrong method fail closed`, async () => {
    const h = build({
      env,
      createClient: () => ({
        rpc: () => ({ data: null, error: { message: "private error" } }),
      }),
    });
    eq(
      (await h(
        new Request("https://example.invalid?check=auth", {
          method,
          headers: { "x-god-mode-token": token },
        }),
      )).status,
      503,
    );
    eq(
      (await h(
        new Request("https://example.invalid?check=auth", {
          method: "DELETE",
          headers: { apikey: key },
        }),
      )).status,
      405,
    );
  });
}
Deno.test("tax amounts preserve unknown vs explicit zero", () => {
  for (const v of [null, undefined, "", "  ", false, {}, "bad", -1, Infinity]) {
    eq(amount(v), null);
  }
  for (const v of [0, "0", " 0.00 "]) eq(amount(v), 0);
  eq(amount("12.50"), 12.5);
});
Deno.test("GIS HTTP200 errors, malformed and truncated results fail closed", async () => {
  for (
    const data of [{ error: { code: 500 } }, {}, {
      features: [],
      exceededTransferLimit: true,
    }]
  ) {
    let failed = false;
    try {
      await gis(
        "https://example.invalid/query",
        async () => Response.json(data),
      );
    } catch {
      failed = true;
    }
    eq(failed, true);
  }
});
function fixture(
  options: {
    tax?: unknown;
    failWetlands?: boolean;
    failUtilities?: boolean;
    failPA?: boolean;
    failWrite?: boolean;
    failOptional?: boolean;
  } = {},
) {
  const patches: any[] = [];
  const propertyPatches: any[] = [];
  const queries: string[] = [];
  const row = {
    id: "review",
    property_id: "subject",
    status: "review_required",
    findings: { underwriting: { preliminary_mao: 99999 } },
    flood_status: "clear",
    wetlands_status: "clear",
    properties: {
      parcel_id: parcel,
      owner_name: "Exact Owner",
      property_type: "land",
      acreage: 1,
      estimated_market_value: 999999,
      assessed_value: 12345,
    },
  };
  const sb = {
    from: (table: string) => {
      queries.push(table);
      let patch: any = null;
      const chain: any = {
        select: () => chain,
        in: () => chain,
        order: () => chain,
        limit: () => chain,
        eq: () => chain,
        update: (v: any) => {
          patch = v;
          (table === "properties" ? propertyPatches : patches).push(v);
          return chain;
        },
        insert: () => chain,
        then: (resolve: any) =>
          resolve({
            data: patch ? null : table === "due_diligence_reviews" ? [row] : [],
            error: options.failWrite && table === "properties"
              ? { message: "write failed" }
              : null,
          }),
      };
      return chain;
    },
  };
  const fetcher = async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("Map_Property_Appraiser")) {
      return Response.json(
        options.failPA ? { error: { code: 500 } } : {
          features: [{
            attributes: {
              PARCELID: parcel,
              NAME: "Exact Owner",
              AMTDUE: options.tax,
            },
            centroid: { x: -81, y: 28 },
          }],
        },
      );
    }
    if (options.failOptional) return Response.json({ error: { code: 503 } });
    if (url.includes("Wetlands")) {
      return Response.json(
        options.failWetlands ? { error: { code: 500 } } : { features: [] },
      );
    }
    if (url.includes("Utilities")) {
      return Response.json(
        options.failUtilities ? { error: { code: 500 } } : { features: [] },
      );
    }
    if (url.includes("parcel_search")) {
      return Response.json({
        features: [1, 2, 3].map((n) => ({
          attributes: { PARNO: String(n), SALE1_AMT: 100000, ACRES: 1 },
        })),
      });
    }
    if (url.includes("?f=json")) {
      return Response.json({ layers: [{ id: 1, name: "Zoning street" }] });
    }
    return Response.json({ features: [] });
  };
  return {
    deps: { env, createClient: () => sb, fetch: fetcher as typeof fetch },
    patches,
    propertyPatches,
    queries,
  };
}
Deno.test("worker: missing tax, failed wetlands/utilities, unqualified comps stay in review", async () => {
  const f = fixture({ failWetlands: true, failUtilities: true });
  const r = await worker(f.deps)(
    new Request("https://example.invalid?limit=1", {
      method: "POST",
      headers: { apikey: key },
    }),
  );
  eq(r.status, 200);
  const p = f.patches[0];
  eq(p.tax_status, "review_required");
  eq(p.findings.tax.amount_due, null);
  eq(p.wetlands_status, "review_required");
  eq(p.findings.wetlands.hit, null);
  eq(p.utilities_status, "review_required");
  eq(p.comps_status, "review_required");
  eq(p.findings.underwriting.preliminary_mao, null);
  eq(p.dd_score, 0);
  eq(p.status, "review_required");
  eq(f.propertyPatches[0].estimated_market_value, undefined);
  eq(f.propertyPatches[0].wetlands_signal, undefined);
});
Deno.test("worker: valid zero tax and no-hit GIS do not create parcel clearance", async () => {
  const f = fixture({ tax: 0 });
  await worker(f.deps)(
    new Request("https://example.invalid", {
      method: "POST",
      headers: { apikey: key },
    }),
  );
  const p = f.patches[0];
  eq(p.tax_status, "current_pa_zero_due");
  eq(p.flood_status, "review_required");
  eq(p.wetlands_status, "review_required");
  eq(p.findings.underwriting.estimated_value, null);
});
for (
  const [name, build] of [["worker", worker], ["finalizer", finalize]] as const
) {
  Deno.test(`${name}: source failure clears stale offer/confidence in persisted review`, async () => {
    const f = fixture({ failPA: true });
    const r = await build(f.deps)(
      new Request("https://example.invalid", {
        method: "POST",
        headers: { apikey: key },
      }),
    );
    eq(r.status, 200);
    eq(f.patches[0].findings.underwriting.preliminary_mao, null);
    eq(f.patches[0].dd_score, 0);
    eq(f.patches[0].status, "review_required");
    eq(f.propertyPatches.length, 0);
    eq((await r.json()).errors, 1);
  });
  Deno.test(`${name}: property write failure is recorded as failure`, async () => {
    const f = fixture({ failWrite: true });
    const r = await build(f.deps)(
      new Request("https://example.invalid", {
        method: "POST",
        headers: { apikey: key },
      }),
    );
    eq((await r.json()).errors, 1);
    eq(
      f.patches.at(-1).findings.source_error.status,
      "source_or_write_failure",
    );
  });
}
Deno.test("finalizer: never relabels a preliminary estimate as assessed value", async () => {
  const f = fixture();
  await finalize(f.deps)(
    new Request("https://example.invalid", {
      method: "POST",
      headers: { apikey: key },
    }),
  );
  const p = f.patches[0];
  eq(p.findings.underwriting.assessed_reference_value, 12345);
  eq(p.findings.underwriting.estimated_value, null);
  eq(p.findings.underwriting.preliminary_mao, null);
  eq(p.dd_score, 0);
});
Deno.test("CRM: authorized response keeps existing contract and does not send outreach", async () => {
  const f = fixture();
  const r = await crm(f.deps)(
    new Request("https://example.invalid?entity=seller", {
      headers: { apikey: key },
    }),
  );
  eq(r.status, 200);
  eq((await r.json()).contacts, []);
  eq(f.queries, ["crm_contact_queue"]);
  eq(f.patches, []);
});

Deno.test("exhausted GIS deadline makes no external request", async () => {
  let calls = 0;
  let failed = false;
  try {
    await gis("https://example.invalid/query", async () => {
      calls++;
      return Response.json({ features: [] });
    }, Date.now() - 1);
  } catch {
    failed = true;
  }
  eq(calls, 0);
  eq(failed, true);
});

Deno.test("worker: optional source outages persist unknown evidence without dropping the review", async () => {
  const f = fixture({ failOptional: true });
  const r = await worker(f.deps)(
    new Request("https://example.invalid", {
      method: "POST",
      headers: { apikey: key },
    }),
  );
  const body = await r.json();
  eq(body.processed, 1);
  eq(body.errors, 0);
  const p = f.patches[0];
  eq(p.findings.flood.source_error, true);
  eq(p.findings.comps.source_error, true);
  eq(p.utilities_status, "review_required");
  eq(p.findings.underwriting.preliminary_mao, null);
});
