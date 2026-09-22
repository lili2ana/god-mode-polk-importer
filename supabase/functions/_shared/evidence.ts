export function amount(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  if (
    typeof value === "string" && !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value.trim())
  ) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}
export function limit(req: Request) {
  const raw = new URL(req.url).searchParams.get("limit") ?? "5";
  if (!/^[1-5]$/.test(raw)) throw new Error("Invalid batch size");
  return Number(raw);
}
export async function gis(
  url: string,
  fetcher: typeof fetch,
  deadline = Date.now() + 10000,
) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw new Error("GIS budget exhausted");
  const r = await fetcher(url, {
    signal: AbortSignal.timeout(Math.min(10000, remaining)),
  });
  if (!r.ok) throw new Error("GIS HTTP failure");
  const data = await r.json();
  if (!data || data.error || data.exceededTransferLimit) {
    throw new Error("GIS incomplete/error response");
  }
  if (
    new URL(url).pathname.endsWith("/query") && !Array.isArray(data.features)
  ) throw new Error("GIS missing features");
  return data;
}
export function requireParcel(data: any, parcel: string) {
  if (
    data.features?.length !== 1 ||
    String(data.features[0]?.attributes?.PARCELID ?? "").replace(
        /[^0-9]/g,
        "",
      ) !== parcel.replace(/[^0-9]/g, "") ||
    !/^\d{18}$/.test(parcel.replace(/[^0-9]/g, ""))
  ) throw new Error("Unresolved parcel");
  return data.features[0];
}
export function pendingUnderwriting(checked_at: string) {
  return {
    checked_at,
    estimated_value: null,
    preliminary_mao: null,
    confidence_score: null,
    eligible_for_automated_acquisition: false,
    note:
      "Verified comparable sales, complete costs, title and legal access review remain required.",
  };
}
export function failedReview(findings: any, checked_at: string) {
  return {
    status: "review_required",
    dd_score: 0,
    title_status: "review_required",
    tax_status: "review_required",
    zoning_status: "review_required",
    access_status: "review_required",
    utilities_status: "review_required",
    flood_status: "review_required",
    wetlands_status: "review_required",
    comps_status: "review_required",
    exit_status: "review_required",
    underwriting_status: "review_required",
    updated_at: checked_at,
    findings: {
      ...findings,
      source_error: { checked_at, status: "source_or_write_failure" },
      underwriting: pendingUnderwriting(checked_at),
    },
  };
}
