"""Read-only Charlotte account + legacy key discovery. Never authorizes writes."""
import json,sys,requests
from run_charlotte_acceptance import PARCEL,LAYERS,query,geom,PROJECT
from shapely.ops import transform
def fields(url):
 r=requests.get(url,params={"f":"json"},timeout=25);r.raise_for_status();j=r.json()
 if "error" in j:raise RuntimeError(str(j["error"]))
 return {f["name"] for f in j["fields"]}
def main():
 pf,rf,vf=(fields(x) for x in (PARCEL,LAYERS["road"],LAYERS["paved"]))
 print("LAYER_FIELDS "+json.dumps({"parcel":sorted(pf),"road":sorted(rf),"pavement":sorted(vf)}),flush=True)
 shared=sorted((rf&vf)&{"SKEY","RKEY"})
 if not shared:raise AssertionError("No common SKEY/RKEY attributes across both layer definitions")
 if "ACCOUNT" not in pf:raise AssertionError("Missing ACCOUNT in parcel layer")
 roads=query(LAYERS["road"],where="1=1",resultRecordCount=100)
 for road in roads:
  ra=road.get("attributes") or {}
  for key in shared:
   val=ra.get(key)
   if val is None or str(val).strip()=="":continue
   v=str(val).replace("'","''")
   paved=query(LAYERS["paved"],where=f"{key}='{v}'",resultRecordCount=30)
   for pavement in paved:
    pg=transform(PROJECT,geom(pavement));rg=transform(PROJECT,geom(road))
    if pg.is_empty or rg.is_empty or not pg.is_valid or not rg.is_valid:continue
    candidates=query(PARCEL,geometry=json.dumps(dict(road["geometry"],spatialReference={"wkid":4326})),geometryType="esriGeometryPolyline",spatialRel="esriSpatialRelIntersects",resultRecordCount=100)
    for parcel in candidates:
     a=parcel.get("attributes") or {};pid=a.get("ACCOUNT")
     if not pid:continue
     g=transform(PROJECT,geom(parcel))
     if not g.is_valid or g.is_empty:continue
     contact=g.boundary.intersection(rg)
     if contact.length<=0 or g.boundary.intersection(pg).is_empty:continue
     print("VERIFIED_LEGACY_CANDIDATE "+json.dumps({"account":str(pid),"key":key,"value":val,"boundary_contact_m":contact.length,"topology_ok":True}),flush=True)
     return 0
 raise AssertionError("No verified legacy candidate in first 100 centerline records; widen search without inventing ID")
if __name__=="__main__":sys.exit(main())
