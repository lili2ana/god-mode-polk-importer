import { type Dependencies, json, protect } from "../_shared/auth.ts";
import {
  amount,
  failedReview,
  gis,
  limit,
  pendingUnderwriting,
  requireParcel,
} from "../_shared/evidence.ts";
export function buildHandler(deps: Dependencies) {
  return protect("god-mode-dd-finalize", "POST", async (req, sb) => {
    const deadline = Date.now() + 25000;
    const J = (url: string) => gis(url, deps.fetch ?? fetch, deadline);
    const PA =
      "https://gis.polk-county.net/server/rest/services/Map_Property_Appraiser/FeatureServer/1/query";
    const FLU =
      "https://gis.polk-county.net/hosting/rest/services/PublicViewer/Map_Land_Use_and_Zoning/MapServer/9/query";
    const ROAD =
      "https://gis.polk-county.net/hosting/rest/services/PolkRoads/Polk_Roads_Map/MapServer/5/query";

    function Q(base: string, p: Record<string, string | number | boolean>) {
      const u = new URL(base);
      for (const [k, v] of Object.entries(p)) u.searchParams.set(k, String(v));
      return u.toString();
    }

    const { data: rows, error } = await sb.from("due_diligence_reviews").select(
      "*,properties(*)",
    ).in("status", [
      "in_progress",
      "queued",
      "machine_complete",
      "review_required",
    ]).order("updated_at", { ascending: true }).limit(limit(req));
    if (error) {
      return new Response(JSON.stringify({ ok: false, error: error.message }), {
        status: 500,
      });
    }
    const out: any[] = [];
    for (const d of rows ?? []) {
      if (Date.now() >= deadline) break;
      const p: any = (d as any).properties;
      const parcel = String(p.parcel_id ?? "");
      const now = new Date().toISOString();
      try {
        const pa = await J(
          Q(PA, {
            where: `PARCELID='${parcel.replace(/'/g, "''")}'`,
            outFields: "PARCELID",
            returnGeometry: true,
            returnCentroid: true,
            outSR: 4326,
            f: "json",
          }),
        );
        const f = requireParcel(pa, parcel);
        let x = f?.centroid?.x, y = f?.centroid?.y;
        if ((x == null || y == null) && f?.geometry?.rings?.[0]) {
          const pts = f.geometry.rings[0];
          x = pts.reduce((s: number, v: any) => s + v[0], 0) / pts.length;
          y = pts.reduce((s: number, v: any) => s + v[1], 0) / pts.length;
        }
        if (x == null || y == null) throw new Error("no centroid");
        const findings: any = { ...(d.findings ?? {}) };
        const flu = await J(
          Q(FLU, {
            geometry: `${x},${y}`,
            geometryType: "esriGeometryPoint",
            inSR: 4326,
            spatialRel: "esriSpatialRelIntersects",
            outFields: "*",
            returnGeometry: false,
            f: "json",
          }),
        );
        const fa = flu.features?.[0]?.attributes ?? null;
        const fluName = fa?.FLUNAME ?? fa?.FLU_NAME ?? fa?.DESCRIPTION ??
          fa?.FLU ?? null;
        const rd = await J(Q(ROAD, {
          geometry: `${x},${y}`,
          geometryType: "esriGeometryPoint",
          inSR: 4326,
          distance: 150,
          units: "esriSRUnit_Meter",
          spatialRel: "esriSpatialRelIntersects",
          outFields: "*",
          returnGeometry: false,
          resultRecordCount: 5,
          f: "json",
        })).catch(() => ({ features: [] }));
        const roadHit = rd.features?.[0]?.attributes ?? null;
        const assessed = amount(p.assessed_value);
        const prelimMao = null;
        findings.zoning = {
          source: "Polk County PublicViewer Future Land Use 2030",
          checked_at: now,
          layer: 9,
          attributes: fa,
          classification: fluName,
          note:
            "Future land use is authoritative county GIS planning data; parcel-specific zoning/legal interpretation may still require jurisdiction review.",
        };
        findings.access = {
          source: "Polk County road centerline GIS",
          checked_at: now,
          nearby_road: roadHit,
          distance_test_meters: 150,
          note:
            "Road-centerline proximity is a machine access screen, not proof of legal ingress/egress or easement rights.",
        };
        findings.comps = {
          ...(findings.comps ?? {}),
          fallback_source: "Stored assessed_value (reference only)",
          fallback_value: assessed,
          fallback_checked_at: now,
          note:
            "Recorded-sale comp automation is not yet authoritative enough; assessor value used only as preliminary fallback.",
        };
        findings.underwriting = {
          ...pendingUnderwriting(now),
          assessed_reference_value: assessed,
        };
        const patch: any = {
          zoning_status: fa ? "future_land_use_verified" : "review_required",
          access_status: roadHit
            ? "mapped_road_proximity_verified"
            : "review_required",
          comps_status: "review_required",
          underwriting_status: "review_required",
          findings,
          dd_score: 0,
          status: "review_required",
          completed_at: now,
          updated_at: now,
        };
        const { error: ue } = await sb.from("due_diligence_reviews").update(
          patch,
        ).eq("id", d.id);
        if (ue) throw ue;
        const pp: any = { updated_at: now };
        if (fluName) pp.future_land_use = fluName;
        if (roadHit) pp.road_access_signal = true;
        const { error: pe } = await sb.from("properties").update(pp).eq(
          "id",
          d.property_id,
        );
        if (pe) throw pe;
        out.push({
          parcel,
          zoning: patch.zoning_status,
          access: patch.access_status,
          comps: patch.comps_status,
          underwriting: patch.underwriting_status,
          preliminary_mao: prelimMao,
          dd_score: patch.dd_score,
        });
      } catch (e) {
        const { error: failureWrite } = await sb.from("due_diligence_reviews")
          .update(failedReview(d.findings, now)).eq("id", d.id);
        out.push({
          parcel,
          error: failureWrite
            ? "review_write_failed"
            : "source_or_write_failure",
        });
      }
    }
    const { error: runError } = await sb.from("automation_runs").insert({
      workflow_name: "god_mode_dd_finalize",
      status: out.some((x: any) => x.error) ? "partial" : "success",
      finished_at: new Date().toISOString(),
      records_processed: out.filter((x: any) => !x.error).length,
      metadata: { results: out },
    });
    if (runError) {
      return json({ ok: false, error: "Run audit write failed" }, 500);
    }
    return new Response(
      JSON.stringify({
        ok: !out.some((r: any) => r.error),
        processed: out.filter((x: any) => !x.error).length,
        errors: out.filter((x: any) => x.error).length,
        results: out,
      }),
      {
        headers: {
          "content-type": "application/json",
          "cache-control": "no-store",
        },
      },
    );
  }, deps);
}
