"""Unified six-parcel, fail-closed GIS acceptance. Read-only; no seller release."""
import json,sys
import requests
from run_charlotte_acceptance import PARCEL,LAYERS,NWI,query,geom,intersect,overlap
LEE="https://gismapserver.leegov.com/gisserver910/rest/services/Layers/ParcelAddress/MapServer/0"
TARGETS=[
 ("lee_hold","334426L3030160020"),
 ("charlotte_flood_reject","402227279013"),
 ("charlotte_lomr","422631100002"),
 ("charlotte_wetlands_agreement","422336100001"),
 ("charlotte_wetlands_discrepancy","422120100001"),
 ("charlotte_legacy_pavement","422120200001"),
]
def by_id(layer,field,value):
    escaped=str(value).replace("'","''")
    matches=query(layer,where=f"{field}='{escaped}'",resultRecordCount=3)
    if len(matches)!=1:raise AssertionError(f"Expected exactly one {field}={value}, found {len(matches)}")
    return matches[0]
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
        raise AssertionError("UNVERIFIED_LEGACY_JOIN: account exists but SKEY/RKEY and boundary adjacency are not proven")
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
