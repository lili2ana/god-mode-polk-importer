#!/usr/bin/env python3
"""Read-only Lee parcel-source check; fail closed on missing evidence."""
import datetime,json,sys,urllib.parse,urllib.request
SOURCES={
"hosted":"https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0",
"county":"https://gismapserver.leegov.com/gisserver910/rest/services/DataExplorer/Parcels/MapServer/0"}
FIELDS={"STRAP","FOLIOID","FRONTAGE","DEPTH","GISACRES","SITEADDR"}
STRAP="334426L3030160020"
def query(url,params):
    req=urllib.request.Request(url+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"GodMode-Lee-readonly-check/1.0"})
    with urllib.request.urlopen(req,timeout=30) as response:
        data=json.load(response)
    if "error" in data: raise ValueError(data["error"])
    return data
def inspect(url):
    meta=query(url,{"f":"json"})
    count=query(url+"/query",{"f":"json","where":"1=1","returnCountOnly":"true"})["count"]
    found={f["name"] for f in meta.get("fields",[])}
    sample=query(url+"/query",{"f":"json","where":"STRAP='"+STRAP+"'","outFields":",".join(sorted(FIELDS & found)),"returnGeometry":"true","outSR":2237})
    return {"count":count,"geometry_type":meta.get("geometryType"),"spatial_reference":meta.get("extent",{}).get("spatialReference"),"missing_fields":sorted(FIELDS-found),"sample":sample.get("features",[]),"license_info":meta.get("copyrightText"),"last_edit_date":meta.get("editingInfo",{}).get("lastEditDate")}
def main():
    result={"checked_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"sources":{}}
    for label,url in SOURCES.items():
        try: result["sources"][label]=inspect(url)
        except Exception as exc: result["sources"][label]={"error":str(exc)}
    a=result["sources"]["hosted"];b=result["sources"]["county"]
    parity=(not any("error" in x for x in (a,b)) and not a["missing_fields"] and not b["missing_fields"] and a["count"]==b["count"] and len(a["sample"])==len(b["sample"])==1 and a["sample"][0]==b["sample"][0])
    result["sample_parity"]=bool(parity)
    result["adapter_decision"]="RETAIN_EXISTING_COUNTY_ADAPTER"
    result["reason"]="Countywide geometry and commercial reuse rights unverified; no automatic cutover even if sample matches."
    print(json.dumps(result,indent=2))
    with open("lee-parcel-comparison.json","w") as f: json.dump(result,f,indent=2)
    return 0 if all("error" not in x for x in (a,b)) else 1
if __name__=="__main__":sys.exit(main())
