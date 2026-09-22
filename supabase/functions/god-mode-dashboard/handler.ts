import { type Dependencies, json, protect } from "../_shared/auth.ts";

export function buildHandler(deps: Dependencies) {
  return protect("god-mode-dashboard", "GET", async (req, sb) => {
    const format = new URL(req.url).searchParams.get("format") ?? "html";
    if (!["html", "json"].includes(format)) {
      return json({ ok: false, error: "Invalid format" }, 400);
    }
    const replies = await Promise.all([
      sb.from("properties").select("id", { count: "exact", head: true }),
      sb.from("properties").select("id", { count: "exact", head: true }).eq(
        "property_type",
        "residential",
      ),
      sb.from("properties").select("id", { count: "exact", head: true }).eq(
        "property_type",
        "land",
      ),
      sb.from("leads").select("id", { count: "exact", head: true }),
      sb.from("leads").select("id", { count: "exact", head: true }).eq(
        "priority",
        "hot",
      ),
    ]);
    // Missing/error counts must never masquerade as an empty inventory.
    if (
      replies.some((r) =>
        r.error || !Number.isSafeInteger(r.count) || r.count < 0
      )
    ) {
      return json({ ok: false, error: "Inventory counts unavailable" }, 503);
    }
    const [properties, residential, land, leads, hotLeads] = replies.map((r) =>
      r.count as number
    );
    const counts = {
      properties,
      residential,
      land,
      preliminary_leads: leads,
      hot_preliminary_leads: hotLeads,
    };
    const generatedAt = new Date().toISOString();
    if (format === "json") {
      return json({
        ok: true,
        counts,
        generated_at: generatedAt,
        interpretation:
          "Inventory counts only; lead priority does not establish qualified underwriting.",
        acquisition_authorized: false,
        outreach_authorized: false,
      });
    }
    const cards = [
      ["Properties", properties],
      ["Residential", residential],
      ["Land", land],
      ["Preliminary leads", leads],
      ["Hot preliminary leads", hotLeads],
    ].map(([label, count]) =>
      `<div class="card"><div class="label">${label}</div><div class="n">${count}</div></div>`
    ).join("");
    // All dynamic HTML values are validated integers or our own ISO timestamp.
    const html =
      `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Real Estate God Mode</title><style>body{font-family:Arial,sans-serif;background:#0b0f14;color:#fff;margin:0;padding:40px}main{max-width:1100px;margin:auto}h1{font-size:36px;margin:0 0 8px}.sub,.label{color:#9fb0c3}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px;margin-top:30px}.card,.status{background:#141b24;border:1px solid #263345;border-radius:16px;padding:22px}.n{font-size:34px;font-weight:700;margin-top:8px}.status{margin-top:28px}</style></head><body><main><h1>REAL ESTATE GOD MODE</h1><p class="sub">Private Polk County inventory</p><div class="grid">${cards}</div><div class="status"><strong>Underwriting review required</strong><p>These are current inventory counts. Preliminary lead priority does not establish property value, a supported offer, or title and legal-access clearance.</p><p>Seller outreach remains disabled. This view does not authorize acquisition or outreach.</p><p class="sub">Counts retrieved ${generatedAt}</p></div></main></body></html>`;
    return new Response(html, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "content-security-policy":
          "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
      },
    });
  }, deps);
}
