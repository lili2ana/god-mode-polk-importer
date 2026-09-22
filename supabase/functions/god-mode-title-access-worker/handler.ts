import { type Dependencies, json, protect } from "../_shared/auth.ts";

const OR_BASE = "https://apps.polkcountyclerk.net/browserviewor/";
const OFFICIAL_RECORDS = "https://www.polkclerkfl.gov/184/Official-Records";

export function buildHandler(deps: Dependencies) {
  return protect("god-mode-title-access-worker", "POST", async (req, sb) => {
    const started = new Date().toISOString();
    const rawLimit = new URL(req.url).searchParams.get("limit") ?? "5";
    if (!/^[1-5]$/.test(rawLimit)) {
      return json({ ok: false, error: "Invalid limit" }, 400);
    }
    const fetcher = deps.fetch ?? fetch;
    async function probe(url: string) {
      try {
        const r = await fetcher(url, {
          method: "GET",
          redirect: "follow",
          signal: AbortSignal.timeout(8000),
        });
        await r.body?.cancel();
        return { ok: r.ok, status: r.status };
      } catch {
        return { ok: false, status: null, source_error: true };
      }
    }
    const { data: rows, error } = await sb.from("due_diligence_reviews")
      .select(
        "id,property_id,title_status,access_status,findings,updated_at,properties(parcel_id,owner_name,road_access_signal)",
      )
      .eq("status", "review_required").eq("access_status", "review_required")
      .order("updated_at", { ascending: true }).limit(Number(rawLimit));
    if (error) return json({ ok: false, error: "Review read failed" }, 500);
    const [browserviewor, officialRecords] = await Promise.all([
      probe(OR_BASE),
      probe(OFFICIAL_RECORDS),
    ]);
    const results: Array<{ ok: boolean; error?: string }> = [];
    for (const d of rows ?? []) {
      const p = d.properties ?? {};
      const findings = { ...(d.findings ?? {}) };
      const now = new Date().toISOString();
      findings.title_records = {
        source: "Polk County Clerk Official Records",
        official_records_url: OFFICIAL_RECORDS,
        browserviewor_url: OR_BASE,
        checked_at: now,
        owner: String(p.owner_name ?? "").trim(),
        parcel: String(p.parcel_id ?? "").trim(),
        source_health: { browserviewor, official_records: officialRecords },
        integration_status: "manual_official_records_review_required",
        automated_clearance: false,
        note:
          "Endpoint reachability is not a title search. Recorded liens and title require official-record review.",
      };
      findings.access_records = {
        source: "Stored road signal; official easement records still required",
        checked_at: now,
        parcel: String(p.parcel_id ?? "").trim(),
        stored_road_access_signal: typeof p.road_access_signal === "boolean"
          ? p.road_access_signal
          : null,
        mapped_road_proximity_verified: false,
        legal_access_verified: false,
        integration_status: "recorded_easement_or_ingress_review_required",
        note:
          "A stored road signal is not freshly verified road proximity or legal ingress/egress.",
      };
      // Compare the read version before writing the findings document. Never overwrite
      // a concurrent DD/human edit or advance title/access/status/underwriting fields.
      let update = sb.from("due_diligence_reviews")
        .update({ findings, updated_at: now }).eq("id", d.id)
        .eq("status", "review_required").eq("access_status", "review_required");
      update = d.updated_at == null
        ? update.is("updated_at", null)
        : update.eq("updated_at", d.updated_at);
      const { data: saved, error: writeError } = await update.select("id");
      results.push(
        writeError || saved?.length !== 1
          ? { ok: false, error: "write_failed_or_concurrent_change" }
          : { ok: true },
      );
    }
    const processed = results.filter((r) => r.ok).length;
    const errors = results.filter((r) => !r.ok).length;
    const { error: auditError } = await sb.from("automation_runs").insert({
      workflow_name: "god_mode_title_access_worker",
      status: errors ? "partial" : "success",
      finished_at: new Date().toISOString(),
      records_processed: processed,
      metadata: {
        version: 5,
        started_at: started,
        browserviewor,
        official_records: officialRecords,
        held_records: rows?.length ?? 0,
        errors,
      },
    });
    return json({
      ok: errors === 0 && !auditError,
      version: 5,
      processed,
      errors,
      audit_error: !!auditError,
      held_records: rows?.length ?? 0,
      source_health: { browserviewor, official_records: officialRecords },
      title_clearance_automated: false,
      access_clearance_automated: false,
    });
  }, deps);
}
