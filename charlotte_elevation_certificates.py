"""Read-only Charlotte County elevation certificate enrichment; never certifies flood safety."""
import json
import urllib.parse
import urllib.request

CERTIFICATES = "https://agis3.charlottecountyfl.gov/arcgis/rest/services/Essentials/CCGISLayers/MapServer/117"
PARCELS = "https://agis3.charlottecountyfl.gov/arcgis/rest/services/Essentials/CCGISLayers/MapServer/27"
CERTIFICATE_FIELDS = ("ParcelID", "ProjectID", "Base_Flood_Elevation", "Floor_Elevation", "Datum", "AppStatus", "LINK")


def arcgis_query(url, params):
    request = urllib.request.Request(
        url + "/query?" + urllib.parse.urlencode({"f": "json", **params}),
        headers={"User-Agent": "GodMode-Charlotte-certificate-reader/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if "error" in result or result.get("exceededTransferLimit"):
        raise ValueError("ArcGIS query error or incomplete page: " + str(result.get("error", "transfer limit")))
    return result.get("features", [])


def certificate_enrichment(account, fetch=arcgis_query):
    """Preserve raw text/datum; reject unsupported assumptions and require manual review."""
    account = str(account).strip()
    if not account.isdigit() or len(account) != 12:
        raise ValueError("Charlotte parcel ACCOUNT must contain exactly 12 digits")
    parcel = fetch(PARCELS, {
        "where": "ACCOUNT='" + account + "'",
        "outFields": "ACCOUNT", "returnGeometry": "false",
    })
    if len(parcel) != 1 or str(parcel[0].get("attributes", {}).get("ACCOUNT")) != account:
        return {"account": account, "status": "REVIEW", "reason": "parcel_match_missing_or_ambiguous",
                "certificates": [], "flood_clearance_verified": False}
    certificates = fetch(CERTIFICATES, {
        "where": "ParcelID='" + account + "'",
        "outFields": ",".join(CERTIFICATE_FIELDS),
        "returnGeometry": "false", "resultRecordCount": 2000,
    })
    records = []
    for item in certificates:
        attrs = item.get("attributes", {})
        if str(attrs.get("ParcelID")) != account:
            raise ValueError("Certificate returned for unexpected parcel")
        records.append({key: attrs.get(key) for key in CERTIFICATE_FIELDS})
    return {"account": account, "status": "REVIEW", "reason": "building_specific_certificate_not_parcel_flood_clearance",
            "certificates": records, "flood_clearance_verified": False,
            "vertical_datum_conversion_verified": False, "source_url": CERTIFICATES}
