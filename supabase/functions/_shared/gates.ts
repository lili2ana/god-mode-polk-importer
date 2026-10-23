// Read-only gates. Only an explicit true result permits downstream work.
export async function eligibleRows(sb: any, rows: any[], stage: "dd" | "enrichment") {
  const eligible: any[] = [];
  for (const row of rows) {
    if (stage === "enrichment" && row.entity_type !== "seller") {
      eligible.push(row);
      continue;
    }
    const { data, error } = await sb.rpc("god_mode_seller_gate", {
      p_property_id: row.property_id, p_stage: stage,
    });
    if (error) throw new Error("Seller gate unavailable");
    if (data === true) eligible.push(row);
  }
  return eligible;
}
