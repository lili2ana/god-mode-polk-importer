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
  const J = (url: string) => gis(url, deps.fetch ?? fetch);
  const PA =
    "https://gis.polk-county.net/server/rest/services/Map_Property_Appraiser/FeatureServer/1/query";
  const DEV =
    "https://gis.polk-county.net/server/rest/services/Map_Development_Overlays/MapServer";
  const STREETS =
    "https://gis.polk-county.net/server/rest/services/Map_Street_and_Addresses/MapServer";
  const UTIL =
    "https://gis.polk-county.net/server/rest/services/Map_Utilities_Service_Area/MapServer";
  const FEMA =
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query";
  const NWI =
    "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer";
  const SWF =
    "https://www25.swfwmd.state.fl.us/arcgis12/rest/services/BaseVector/parcel_search/MapServer/14/query";

  function q(base: string, params: Record<string, string | number | boolean>) {
    const u = new URL(base);
    for (const [k, v] of Object.entries(params)) {
      u.searchParams.set(k, String(v));
    }
    return u.toString();
  }
  function norm(s: any) {
    return String(s ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }
  function median(a: number[]) {
    if (!a.length) return null;
    const s = [...a].sort((x, y) => x - y);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  async function layerByName(base: string, re: RegExp) {
    const m = await J(base + "?f=json");
    return (m.layers ?? []).find((x: any) => re.test(String(x.name ?? "")))
      ?.id ?? null;
  }
  async function pointHit(
    base: string,
    layer: number,
    x: number,
    y: number,
    outFields = "*",
  ) {
    return await J(
      q(`${base}/${layer}/query`, {
        geometry: `${x},${y}`,
        geometryType: "esriGeometryPoint",
        inSR: 4326,
        spatialRel: "esriSpatialRelIntersects",
        outFields,
        returnGeometry: false,
        f: "json",
      }),
    );
  }

  return protect("god-mode-dd-worker", "POST", async (req, sb) => {
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
        headers: { "content-type": "application/json" },
      });
    }
    let zoningLayer: number | null = null, streetLayer: number | null = null;
    try {
      zoningLayer = await layerByName(
        DEV,
        /zoning|future.?land.?use|land.?use/i,
      );
    } catch (_) {}
    try {
      streetLayer = await layerByName(STREETS, /street|road|centerline/i);
    } catch (_) {}
    const results: any[] = [];
    for (const d of rows ?? []) {
      const p: any = (d as any).properties;
      const parcel = String(p?.parcel_id ?? "");
      const started = new Date().toISOString();
      try {
        const pa = await J(
          q(PA, {
            where: `PARCELID='${parcel.replace(/'/g, "''")}'`,
            outFields: "*",
            returnGeometry: true,
            returnCentroid: true,
            outSR: 4326,
            f: "json",
          }),
        );
        const f = requireParcel(pa, parcel);
        const a = f?.attributes ?? {};
        let x = f?.centroid?.x, y = f?.centroid?.y;
        if ((x == null || y == null) && f?.geometry?.rings?.[0]) {
          const pts = f.geometry.rings[0];
          x = pts.reduce((s: number, v: any) => s + v[0], 0) / pts.length;
          y = pts.reduce((s: number, v: any) => s + v[1], 0) / pts.length;
        }
        if (x == null || y == null) {
          throw new Error("parcel geometry unavailable");
        }
        const old = d.findings ?? {};
        const findings: any = { ...old };
        const patch: any = { updated_at: started };

        const ownerNow = String(a.NAME ?? "").trim();
        const ownerStored = String(p.owner_name ?? "").trim();
        const ownerMatch = !!ownerNow && !!ownerStored &&
          (norm(ownerNow) === norm(ownerStored));
        patch.title_status = ownerMatch
          ? "owner_name_match_current_pa"
          : "review_required";
        findings.title = {
          source: "Polk County Property Appraiser",
          checked_at: started,
          current_owner: ownerNow,
          stored_owner: ownerStored,
          owner_match: ownerMatch,
          note:
            "Current ownership consistency check only; not a title commitment or lien search.",
        };

        const amt = amount(a.AMTDUE);
        patch.tax_status = amt !== null
          ? (amt === 0 ? "current_pa_zero_due" : "current_pa_balance_due")
          : "review_required";
        findings.tax = {
          source: "Polk County Property Appraiser",
          checked_at: started,
          amount_due: amt,
          taxable_value: a.TAXVAL ?? null,
          note:
            "PA amount due is recorded separately from verified delinquency/tax-deed status.",
        };

        if (true) {
          const z = await J(q(FEMA, {
            geometry: `${x},${y}`,
            geometryType: "esriGeometryPoint",
            inSR: 4326,
            spatialRel: "esriSpatialRelIntersects",
            outFields: "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
            returnGeometry: false,
            f: "json",
          }));
          const fa = z.features?.[0]?.attributes;
          patch.flood_status = fa ? "point_screen_only" : "review_required";
          findings.flood = {
            source: "FEMA NFHL",
            checked_at: started,
            zone: fa?.FLD_ZONE ?? null,
            subtype: fa?.ZONE_SUBTY ?? null,
            sfha: fa?.SFHA_TF ?? null,
          };
        }
        if (true) {
          let wet: any = null;
          let failures = 0;
          for (const L of [0, 1, 2, 3, 4, 5]) {
            try {
              const z = await J(
                q(`${NWI}/${L}/query`, {
                  geometry: `${x},${y}`,
                  geometryType: "esriGeometryPoint",
                  inSR: 4326,
                  spatialRel: "esriSpatialRelIntersects",
                  outFields: "*",
                  returnGeometry: false,
                  f: "json",
                }),
              );
              if (z.features?.length) {
                wet = { layer: L, attributes: z.features[0].attributes };
                break;
              }
            } catch (_) {
              failures++;
            }
          }
          patch.wetlands_status = wet ? "point_screen_only" : "review_required";
          findings.wetlands = {
            source: "USFWS NWI",
            checked_at: started,
            hit: wet ? true : null,
            failed_layers: failures,
            scope: "point_only_not_parcel_clearance",
            layer: wet?.layer ?? null,
            attributes: wet?.attributes ?? null,
          };
        }

        if (zoningLayer != null) {
          const z = await pointHit(DEV, zoningLayer, x, y, "*");
          const za = z.features?.[0]?.attributes ?? null;
          patch.zoning_status = za ? "verified_gis" : "review_required";
          findings.zoning = {
            source: "Polk County GIS Development Overlays",
            checked_at: started,
            layer: zoningLayer,
            attributes: za,
          };
        } else {
          patch.zoning_status = "review_required";
          findings.zoning = {
            source: "Polk County GIS",
            checked_at: started,
            note:
              "No zoning/land-use layer auto-discovered in Development Overlays; human jurisdiction review required.",
          };
        }

        if (streetLayer != null) {
          const s = await J(q(`${STREETS}/${streetLayer}/query`, {
            geometry: `${x},${y}`,
            geometryType: "esriGeometryPoint",
            inSR: 4326,
            distance: 150,
            units: "esriSRUnit_Meter",
            spatialRel: "esriSpatialRelIntersects",
            outFields: "*",
            returnGeometry: false,
            f: "json",
          }));
          const sa = s.features?.[0]?.attributes ?? null;
          patch.access_status = sa
            ? "verified_near_mapped_street"
            : "review_required";
          findings.access = {
            source: "Polk County GIS Streets",
            checked_at: started,
            layer: streetLayer,
            nearby_street: sa,
            note:
              "Mapped-street proximity is not legal ingress/egress title verification.",
          };
        } else patch.access_status = "review_required";

        const [w, sewer] = await Promise.all([
          pointHit(UTIL, 3, x, y, "SYSTEMNAME").catch(() => null),
          pointHit(UTIL, 2, x, y, "SYSTEMNAME").catch(() => null),
        ]);
        const wa = w?.features?.[0]?.attributes ?? null,
          swa = sewer?.features?.[0]?.attributes ?? null;
        patch.utilities_status = w && sewer && (wa || swa)
          ? "service_area_verified"
          : "review_required";
        findings.utilities = {
          source: "Polk County Utilities GIS",
          checked_at: started,
          water: wa,
          wastewater: swa,
          source_error: !w || !sewer,
          scope: "point_screen_only",
        };

        const comps = await J(q(SWF, {
          geometry: `${x},${y}`,
          geometryType: "esriGeometryPoint",
          inSR: 4326,
          distance: 5,
          units: "esriSRUnit_StatuteMile",
          spatialRel: "esriSpatialRelIntersects",
          outFields:
            "PARNO,PARUSEDESC,ACRES,PARVAL,SALE1_AMT,SALE1_DATE,YRBLT_ACT,SITEADD",
          returnGeometry: false,
          resultRecordCount: 100,
          f: "json",
        }));
        const subjAc = Number(p.acreage ?? 0),
          isLand = String(p.property_type ?? "").toLowerCase() === "land";
        const sales = (comps.features ?? []).map((c: any) => c.attributes ?? {})
          .filter((c: any) =>
            Number(c.SALE1_AMT) > 0 && String(c.PARNO ?? "") !== parcel
          ).slice(0, 30);
        let vals: number[] = [];
        if (isLand && subjAc > 0) {
          vals = sales.filter((c: any) => Number(c.ACRES) > 0).map((c: any) =>
            Number(c.SALE1_AMT) / Number(c.ACRES) * subjAc
          ).filter(Number.isFinite);
        } else {vals = sales.map((c: any) => Number(c.SALE1_AMT)).filter(
            Number.isFinite,
          );}
        const est = median(vals);
        patch.comps_status = "review_required";
        findings.comps = {
          source:
            "SWFWMD Polk County Parcels / Property Appraiser sales fields",
          checked_at: started,
          count: vals.length,
          estimated_value: null,
          unqualified_candidate_median: est,
          qualification: "not_verified",
          method: isLand
            ? "median nearby recorded sale price per acre x subject acreage"
            : "median nearby recorded sale amount",
          sample: sales.slice(0, 10),
        };

        const { data: bm } = await sb.from("buyer_matches").select(
          "match_score",
        ).eq("property_id", d.property_id).order("match_score", {
          ascending: false,
        }).limit(20);
        const matches = bm ?? [];
        const maxMatch = matches.length
          ? Math.max(...matches.map((m: any) => Number(m.match_score) || 0))
          : 0;
        patch.exit_status = matches.length
          ? "buyer_path_verified"
          : "review_required";
        findings.exit = {
          buyer_matches: matches.length,
          max_match_score: maxMatch,
          checked_at: started,
        };

        const mao = null;
        patch.underwriting_status = "review_required";
        findings.underwriting = pendingUnderwriting(started);

        patch.dd_score = 0;
        patch.findings = findings;
        patch.status = "review_required";
        patch.completed_at = new Date().toISOString();
        const { error: ue } = await sb.from("due_diligence_reviews").update(
          patch,
        ).eq("id", d.id);
        if (ue) throw ue;
        const propPatch: any = { updated_at: started };
        if (patch.access_status !== "review_required") {
          propPatch.road_access_signal = true;
        }
        if (wa || swa) propPatch.utility_signal = true;
        if (findings.flood?.zone) propPatch.flood_zone = findings.flood.zone;
        if (findings.wetlands?.hit === true) propPatch.wetlands_signal = true;
        const { error: pe } = await sb.from("properties").update(propPatch).eq(
          "id",
          d.property_id,
        );
        if (pe) throw pe;
        results.push({
          parcel,
          status: patch.status,
          dd_score: patch.dd_score,
          title: patch.title_status,
          tax: patch.tax_status,
          zoning: patch.zoning_status,
          access: patch.access_status,
          utilities: patch.utilities_status,
          comps: patch.comps_status,
          exit: patch.exit_status,
          underwriting: patch.underwriting_status,
          estimated_value: null,
          mao,
        });
      } catch (e) {
        const { error: failureWrite } = await sb.from("due_diligence_reviews")
          .update(failedReview(d.findings, started)).eq("id", d.id);
        results.push({
          parcel,
          status: "error",
          error: failureWrite
            ? "review_write_failed"
            : "source_or_write_failure",
        });
      }
    }
    const { error: runError } = await sb.from("automation_runs").insert({
      workflow_name: "god_mode_dd_worker",
      status: results.some((r: any) => r.status === "error")
        ? "partial"
        : "success",
      finished_at: new Date().toISOString(),
      records_processed: results.filter((r: any) =>
        r.status !== "error"
      ).length,
      metadata: { results },
    });
    if (runError) {
      return json({ ok: false, error: "Run audit write failed" }, 500);
    }
    return new Response(
      JSON.stringify({
        ok: true,
        processed: results.filter((r: any) => r.status !== "error").length,
        errors: results.filter((r: any) => r.status === "error").length,
        results,
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
