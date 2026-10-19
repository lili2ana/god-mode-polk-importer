"""Read-only Charlotte account + legacy key discovery. Never authorizes writes."""
import json,sys,requests
from urllib.parse import urlencode
from run_charlotte_acceptance import PARCEL,LAYERS,query,geom,PROJECT
from shapely.ops import transform
def fields(url):
 r=requests.get(url,params={"f":"json"},timeout=25);r.raise_for_status();j=r.json()
 if "error" in j:raise RuntimeError(str(j["error"]))
 return {f["name"]:f["type"] for f in j["fields"]}
def main():
 pf,rf,vf=(fields(x) for x in (PARCEL,LAYERS["road"],LAYERS["paved"]))
 print("LAYER_FIELDS "+json.dumps({"parcel":pf,"road":rf,"pavement":vf}),flush=True)
 shared=sorted((set(rf)&set(vf))&{"SKEY","RKEY"})
 if not shared:raise AssertionError("No common SKEY/RKEY attributes across both layer definitions")
 if "ACCOUNT" not in pf:raise AssertionError("Missing ACCOUNT in parcel layer")
 seed_params={"f":"json","where":"1=1","outFields":"OBJECTID,SKEY,RKEY,FULLNAME","returnGeometry":"true","outSR":4326,"resultRecordCount":10}
 seed_url=LAYERS["road"]+"/query"
 print("ROAD_SEED_REQUEST "+json.dumps({"url":seed_url+"?"+urlencode(seed_params),"params":seed_params}),flush=True)
 response=requests.get(seed_url,params=seed_params,timeout=25)
 try:
  response.raise_for_status()
  seed_data=response.json()
  if "error" in seed_data:raise RuntimeError(str(seed_data["error"]))
 except Exception as exc:
  print("ROAD_SEED_FAILURE "+json.dumps({"url":response.url,"status":response.status_code,"body":response.text[:800],"error":str(exc)}),flush=True)
  raise
 roads=seed_data.get("features",[])
 print("ROAD_SEED_OK "+json.dumps({"count":len(roads),"url":response.url}),flush=True)
 if not roads:raise AssertionError("No Layer 113 road seeds returned")
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
