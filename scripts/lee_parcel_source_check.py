#!/usr/bin/env python3
"""Read-only Lee parcel source validation. No adapter changes or database writes."""
import datetime
import json
import math
import sys
import urllib.parse
import urllib.request

SOURCES = {
    "hosted": "https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0",
    "county": "https://gismapserver.leegov.com/gisserver910/rest/services/DataExplorer/Parcels/MapServer/0",
}
FIELDS = {"STRAP", "FOLIOID", "FRONTAGE", "DEPTH", "GISACRES", "SITEADDR"}
ANCHOR = "334426L3030160020"
SAMPLE_SIZE = 20
TOLERANCE_FEET = 0.01

def query(url, params):
    request = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "GodMode-Lee-readonly-check/2.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if "error" in result:
        raise ValueError(result["error"])
    return result

def ring_distance(a, b):
    """Cyclic and direction-invariant vertex distance; excludes optional ring closure."""
    if a and a[0] == a[-1]:
        a = a[:-1]
    if b and b[0] == b[-1]:
        b = b[:-1]
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    best = math.inf
    for orientation in (b, list(reversed(b))):
        for shift in range(n):
            worst = max(math.hypot(
                float(a[i][0]) - float(orientation[(i + shift) % n][0]),
                float(a[i][1]) - float(orientation[(i + shift) % n][1]),
            ) for i in range(n))
            best = min(best, worst)
    return best

def geometry_difference(a, b):
    """Conservative comparison: differing ring/vertex counts require review."""
    rings_a = a.get("rings", [])
    rings_b = b.get("rings", [])
    if len(rings_a) != len(rings_b):
        return None
    # Match each ring once, regardless of ring order.
    remaining = list(rings_b)
    differences = []
    for ring in rings_a:
        options = [(ring_distance(ring, candidate), i) for i, candidate in enumerate(remaining)]
        options = [(distance, i) for distance, i in options if distance is not None]
        if not options:
            return None
        distance, index = min(options)
        differences.append(distance)
        remaining.pop(index)
    return max(differences, default=0.0)

def inspect(url):
    meta = query(url, {"f": "json"})
    found = {field["name"] for field in meta.get("fields", [])}
    count = query(url + "/query", {"f": "json", "where": "1=1", "returnCountOnly": "true"})["count"]
    ids = query(url + "/query", {
        "f": "json", "where": "STRAP IS NOT NULL", "returnGeometry": "false",
        "outFields": "STRAP", "orderByFields": "STRAP ASC", "resultRecordCount": SAMPLE_SIZE,
    }).get("features", [])
    straps = sorted({ANCHOR} | {str(item.get("attributes", {}).get("STRAP")) for item in ids
                                if item.get("attributes", {}).get("STRAP")})
    return {
        "count": count,
        "geometry_type": meta.get("geometryType"),
        "wkid": meta.get("extent", {}).get("spatialReference", {}).get("latestWkid"),
        "missing_fields": sorted(FIELDS - found),
        "copyright": meta.get("copyrightText", ""),
        "sample_straps": straps,
    }

def fetch_parcels(url, straps):
    where = "STRAP IN (" + ",".join("'" + s.replace("'", "''") + "'" for s in straps) + ")"
    response = query(url + "/query", {
        "f": "json", "where": where, "outFields": ",".join(sorted(FIELDS)),
        "returnGeometry": "true", "outSR": 2237, "resultRecordCount": 200,
    })
    if response.get("exceededTransferLimit"):
        raise ValueError("Sample query exceeded transfer limit")
    grouped = {}
    for feature in response.get("features", []):
        key = feature.get("attributes", {}).get("STRAP")
        grouped.setdefault(key, []).append(feature)
    return grouped

def main():
    report = {
        "checked_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tolerance_feet": TOLERANCE_FEET,
        "sample_target": SAMPLE_SIZE + 1,
        "sources": {},
        "parcels": [],
        "adapter_decision": "RETAIN_EXISTING_COUNTY_ADAPTER",
        "countywide_geometry_parity_verified": False,
        "commercial_reuse_rights_verified": False,
    }
    try:
        for name, url in SOURCES.items():
            report["sources"][name] = inspect(url)
        a, b = report["sources"]["hosted"], report["sources"]["county"]
        straps = sorted(set(a["sample_straps"]) | set(b["sample_straps"]))
        hosted, county = fetch_parcels(SOURCES["hosted"], straps), fetch_parcels(SOURCES["county"], straps)
        for strap in straps:
            ha, cb = hosted.get(strap, []), county.get(strap, [])
            entry = {"strap": strap, "hosted_matches": len(ha), "county_matches": len(cb)}
            if len(ha) == len(cb) == 1:
                attr_a, attr_b = ha[0]["attributes"], cb[0]["attributes"]
                entry["attributes_equal"] = attr_a == attr_b
                entry["max_vertex_difference_feet"] = geometry_difference(
                    ha[0].get("geometry", {}), cb[0].get("geometry", {})
                )
                entry["geometry_within_tolerance"] = (
                    entry["max_vertex_difference_feet"] is not None
                    and entry["max_vertex_difference_feet"] <= TOLERANCE_FEET
                )
                entry["pass"] = entry["attributes_equal"] and entry["geometry_within_tolerance"]
            else:
                entry["pass"] = False
            report["parcels"].append(entry)
        report["sample_pass"] = (
            a["count"] == b["count"]
            and a["geometry_type"] == b["geometry_type"] == "esriGeometryPolygon"
            and a["wkid"] == b["wkid"] == 2237
            and not a["missing_fields"] and not b["missing_fields"]
            and bool(report["parcels"])
            and all(row["pass"] for row in report["parcels"])
        )
        report["reason"] = (
            "Sample is only a screening gate; countywide ID/geometry parity and commercial reuse rights unverified."
        )
    except Exception as error:
        report["error"] = str(error)
        report["sample_pass"] = False
    with open("lee-parcel-comparison.json", "w") as output:
        json.dump(report, output, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if "error" not in report else 1

if __name__ == "__main__":
    sys.exit(main())
