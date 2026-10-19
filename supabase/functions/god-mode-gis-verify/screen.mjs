export const parcelSource = 'https://gismapserver.leegov.com/gisserver910/rest/services/DataExplorer/Parcels/MapServer/0/query';
export const screens = [
  ['FEMA_FLOOD', 'https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query', 'FLD_ZONE,ZONE_SUBTY,SFHA_TF'],
  ['USFWS_WETLANDS', 'https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query', 'ATTRIBUTE,WETLAND_TYPE'],
  ['LEE_ROAD', 'https://gismapserver.leegov.com/gisserver910/rest/services/Layers/RoadCenterlines/MapServer/2/query', 'OBJECTID,STREET,SURFACE,MAINTAINBY,ROW_WIDTH'],
];
export function completeFeatures(data) {
  if (!data || data.error || data.exceededTransferLimit || !Array.isArray(data.features)) throw new Error('Incomplete GIS response');
  return data.features;
}
export function parcelGeometry(data, parcel) {
  const features = completeFeatures(data);
  if (features.length !== 1 || features[0]?.attributes?.STRAP !== parcel) throw new Error('Unresolved parcel identity');
  const geometry = features[0].geometry;
  if (!geometry?.rings?.length || geometry.rings.some(r => r.length < 4 || r.some(p => !Number.isFinite(p[0]) || !Number.isFinite(p[1])))) throw new Error('Invalid parcel polygon');
  return {rings: geometry.rings, spatialReference: {wkid: 4326}};
}
export function screenQuery(source, fields, geometry, road = false) {
  const url = new URL(source);
  const params = {geometry: JSON.stringify(geometry), geometryType: 'esriGeometryPolygon', inSR: '4326', outSR: '4326', spatialRel: 'esriSpatialRelIntersects', outFields: fields, returnGeometry: road ? 'true' : 'false', f: 'json'};
  if (road) Object.assign(params, {distance: '30', units: 'esriSRUnit_Meter'});
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  return url.toString();
}
export async function runScreens(geometry, fetcher, save) {
  return Promise.all(screens.map(async ([source, base, fields]) => {
    const road = source === 'LEE_ROAD';
    const url = screenQuery(base, fields, geometry, road);
    let result, details;
    try {
      const response = await fetcher(url, {signal: AbortSignal.timeout(12000)});
      if (!response.ok) throw new Error('GIS HTTP failure');
      const features = completeFeatures(await response.json());
      result = features.length ? 'FOUND' : 'NOT_FOUND';
      details = {checked_at: new Date().toISOString(), feature_count: features.length, method: road ? '30m_full_polygon_buffer_not_verified_frontage' : 'full_parcel_polygon_intersection', features, screening_clearance: false};
      if (road) Object.assign(details, {frontage_verified: false, legal_access_verified: false, paved_candidate_count: features.filter(f => String(f.attributes?.SURFACE).trim().toUpperCase() === 'PAVED').length});
    } catch {
      result = 'ERROR';
      details = {checked_at: new Date().toISOString(), source_error: true, screening_clearance: false};
    }
    // A failed persistence operation must fail the request, not become source evidence.
    await save(source, url, result, details);
    return {source, result, feature_count: details.feature_count ?? null};
  }));
}
