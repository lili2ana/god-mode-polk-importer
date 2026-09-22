import { type Dependencies, json, protect } from "../_shared/auth.ts";
export function buildHandler(deps: Dependencies) {
  return protect("god-mode-crm-feed", "GET", async (req, sb) => {
    const u = new URL(req.url);
    const entity = u.searchParams.get("entity") || "all";
    let q = sb.from("crm_contact_queue").select("*").in("crm_status", [
      "ready_for_crm",
      "needs_enrichment",
    ]).order("priority_score", { ascending: false }).limit(200);
    if (entity === "seller" || entity === "buyer") {
      q = q.eq("entity_type", entity);
    }
    const { data, error } = await q;
    if (error) {
      return new Response(JSON.stringify({ ok: false, error: error.message }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
    const rows = (data ?? []).map((r: any) => ({
      id: r.id,
      entity_type: r.entity_type,
      entity_id: r.entity_id,
      property_id: r.property_id,
      name: r.contact_name,
      company: r.company,
      phone: r.phone,
      email: r.email,
      mailing_address: r.mailing_address,
      priority_score: r.priority_score,
      source_name: r.source_name,
      source_url: r.source_url,
      compliance: {
        consent_status: r.consent_status,
        dnc_status: r.dnc_status,
        sms_status: r.sms_status,
        email_status: r.email_status,
      },
      crm_status: r.crm_status,
      payload: r.payload,
    }));
    return new Response(
      JSON.stringify({ ok: true, count: rows.length, contacts: rows }),
      {
        headers: {
          "content-type": "application/json",
          "cache-control": "no-store",
        },
      },
    );
  }, deps);
}
