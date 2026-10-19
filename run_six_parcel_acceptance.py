"""Unified six-parcel, fail-closed GIS acceptance. Read-only; no seller release."""
import json,sys
from shapely.ops import transform
from pyproj import Transformer
from run_charlotte_acceptance import PROJECT
import requests
from run_charlotte_acceptance import PARCEL,LAYERS,NWI,query,geom,intersect,overlap
LEE="https://gismapserver.leegov.com/gisserver910/rest/services/Layers/ParcelAddress/MapServer/0"
TARGETS=[
 ("lee_hold","334426L3030160020"),
 ("charlotte_flood_reject","402227279013"),
 ("charlotte_lomr","422631100002"),
 ("charlotte_wetlands_agreement","422336100001"),
 ("charlotte_wetlands_discrepancy","422120100001"),
 ("charlotte_legacy_pavement","402301101001"),
]
def by_id(layer,field,value):
    escaped=str(value).replace("'","''")
    matches=query(layer,where=f"{field}='{escaped}'",resultRecordCount=3)
    if len(matches)!=1:raise AssertionError(f"Expected exactly one {field}={value}, found {len(matches)}")
    return matches[0]
def envelope_query(layer,params,fields):
    response=requests.get(layer+"/query",params={"f":"json","outSR":4326,"returnGeometry":"true","outFields":fields,**params},timeout=25)
    response.raise_for_status()
    data=response.json()
    if "error" in data:raise RuntimeError(str(data["error"]))
    return data.get("features",[])

def verify_legacy_join(parcel):
    """Require actual county join keys AND shared boundary geometry, not proximity."""
    # Envelope retrieves nearby candidates; it does not establish legal frontage.
    parcel_metric=transform(PROJECT,geom(parcel))
    search_metric=parcel_metric.buffer(20).envelope
    inverse=Transformer.from_crs("EPSG:26917","EPSG:4326",always_xy=True).transform
    minx,miny,maxx,maxy=transform(inverse,search_metric).bounds
    params={"geometry":f"{minx},{miny},{maxx},{maxy}","geometryType":"esriGeometryEnvelope","inSR":4326,"spatialRel":"esriSpatialRelIntersects","resultRecordCount":100}
    roads=envelope_query(LAYERS["road"],params,"OBJECTID,SKEY,RKEY,FULLNAME")
    pavement=envelope_query(LAYERS["paved"],params,"OBJECTID,SKEY,RKEY,PAVED,DATE_PAVED")
    if not roads or not pavement:
        raise AssertionError("UNVERIFIED_LEGACY_JOIN: no road/pavement candidates within 20m envelope")
    boundary=transform(PROJECT,geom(parcel)).boundary
    if not boundary.is_valid or boundary.is_empty:
        raise AssertionError("UNVERIFIED_LEGACY_JOIN: invalid parcel boundary")
    for road in roads:
        ra=road.get("attributes") or {}
        for paved in pavement:
            pa=paved.get("attributes") or {}
            shared=[]
            for key in ("SKEY","RKEY"):
                rv=ra.get(key);pv=pa.get(key)
                if rv is not None and pv is not None and str(rv).strip() and str(rv)==str(pv):
                    shared.append(key)
            if not shared:continue
            if any(ra.get(k)!=pa.get(k) for k in ("SKEY","RKEY")):continue
            road_geom=transform(PROJECT,geom(road))
            paved_geom=transform(PROJECT,geom(paved))
            if road_geom.is_empty or paved_geom.is_empty or not road_geom.is_valid or not paved_geom.is_valid:
                raise AssertionError("UNVERIFIED_LEGACY_JOIN: invalid centerline geometry")
            # Projected feet/metres: EPSG:26917 uses metres. No distance-only shortcut.
            contact=boundary.intersection(road_geom)
            if contact.is_empty or contact.length<=0:continue
            if boundary.intersection(paved_geom).is_empty:continue
            return {"join_keys":shared,"boundary_contact_m":round(contact.length,3),"road_attributes":{k:ra.get(k) for k in ("SKEY","RKEY")},"pavement_attributes":{k:pa.get(k) for k in ("SKEY","RKEY")}}
    raise AssertionError("UNVERIFIED_LEGACY_JOIN: nearby SKEY/RKEY records do not establish measurable shared boundary; authoritative ROW geometry required")
def assess_charlotte(scenario,pid):
    p=by_id(PARCEL,"ACCOUNT",pid)
    g=geom(p)
    if not g.is_valid or g.is_empty:raise AssertionError("Invalid parcel geometry")
    overlaps={key:overlap(g,intersect(url,p)) for key,url in
      [("flood",LAYERS["flood"]),("lomr",LAYERS["lomr"]),("county_wetlands",LAYERS["wetland"]),("nwi_wetlands",NWI)]}
    assert all(0<=v<=100 for v in overlaps.values())
    difference=abs(overlaps["county_wetlands"]-overlaps["nwi_wetlands"])
    flags=["LEGAL_ACCESS_UNVERIFIED"]
    if overlaps["lomr"]>0:flags.append("LOMR_REVIEW")
    if difference>=10:flags.append("WETLANDS_MATERIAL_DISCREPANCY")
    checks={
      "charlotte_flood_reject":overlaps["flood"]>0,
      "charlotte_lomr":overlaps["lomr"]>0,
      "charlotte_wetlands_agreement":difference<10 and overlaps["county_wetlands"]>0 and overlaps["nwi_wetlands"]>0,
      "charlotte_wetlands_discrepancy":difference>=10,
    }
    if scenario=="charlotte_legacy_pavement":
        join=verify_legacy_join(p)
        return {"scenario":scenario,"parcel_id":pid,"topology_ok":True,"overlap_pct":overlaps,"flags":flags,"legacy_join":join,"assertions_passed":True}
    if not checks[scenario]:raise AssertionError(f"Scenario assertion failed: {scenario}")
    return {"scenario":scenario,"parcel_id":pid,"topology_ok":True,"overlap_pct":overlaps,"flags":flags,"assertions_passed":True}
def assess_lee(pid):
    p=by_id(LEE,"STRAP",pid)
    g=geom(p)
    if not g.is_valid or g.is_empty:raise AssertionError("Invalid Lee geometry")
    return {"scenario":"lee_hold","parcel_id":pid,"topology_ok":True,"flags":["LEGAL_ACCESS_UNVERIFIED"],"assertions_passed":True,"scope":"parcel geometry and hold flag only; not complete Lee flood/NWI qualification"}
def main():
    passed=0;seen=set()
    for scenario,pid in TARGETS:
        try:
            if pid is None:raise AssertionError("Missing verified sixth Charlotte ACCOUNT; no substitution permitted")
            if pid in seen:raise AssertionError("Duplicate target parcel ID")
            seen.add(pid)
            result=assess_lee(pid) if scenario=="lee_hold" else assess_charlotte(scenario,pid)
            passed+=1
            print("PARCEL_PASS "+json.dumps(result),flush=True)
        except Exception as exc:
            print("PARCEL_FAIL "+json.dumps({"scenario":scenario,"parcel_id":pid,"error":str(exc)[:350]}),flush=True)
            break
    print("SIX_PARCEL_GATE "+json.dumps({"passed":passed,"required":6,"gate_pass":passed==6,"db_writes_authorized":False}),flush=True)
    return 0 if passed==6 else 1
if __name__=="__main__":sys.exit(main())
