"""Read-only Charlotte account + legacy key discovery. Never authorizes writes."""
import json,sys,requests
from urllib.parse import urlencode
from run_charlotte_acceptance import PARCEL,LAYERS,query,geom,PROJECT
from shapely.ops import transform
from shapely.geometry import mapping
from pyproj import Transformer
def fields(url):
 r=requests.get(url,params={"f":"json"},timeout=25);r.raise_for_status();j=r.json()
 if "error" in j:raise RuntimeError(str(j["error"]))
 return {f["name"]:f["type"] for f in j["fields"]}
def diagnostic_query(layer,params,label):
 url=layer+"/query"
 response=requests.get(url,params=params,timeout=25)
 try:
  response.raise_for_status()
  data=response.json()
  if "error" in data:raise RuntimeError(str(data["error"]))
 except Exception as exc:
  print(label+"_FAILURE "+json.dumps({"url":response.url,"params":params,"http_status":response.status_code,"body":response.text[:1200],"error":str(exc)}),flush=True)
  raise
 print(label+"_OK "+json.dumps({"count":len(data.get("features",[]))}),flush=True)
 return data.get("features",[])
def literal(value,field_type):
 if field_type in ("esriFieldTypeInteger","esriFieldTypeSmallInteger","esriFieldTypeOID"):
  return str(int(value))
 if field_type=="esriFieldTypeString":
  return "'"+str(value).replace("'","''")+"'"
 raise AssertionError("Unsupported join field type "+field_type)
def main():
 pf,rf,vf=(fields(x) for x in (PARCEL,LAYERS["road"],LAYERS["paved"]))
 print("LAYER_FIELDS "+json.dumps({"parcel":pf,"road":rf,"pavement":vf}),flush=True)
 shared=sorted((set(rf)&set(vf))&{"SKEY","RKEY"})
 if not shared:raise AssertionError("No common SKEY/RKEY attributes across both layer definitions")
 if "ACCOUNT" not in pf:raise AssertionError("Missing ACCOUNT in parcel layer")
 seed_params={"f":"json","where":"1=1","outFields":"OBJECTID,SKEY,RKEY,FULLNAME","returnGeometry":"true","outSR":4326,"resultRecordCount":60}
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
 inverse=Transformer.from_crs(26917,4326,always_xy=True).transform
 # Screening envelope only, not surveyed right-of-way or proof of legal access.
 buffer_m=20
 for road in roads:
  ra=road.get("attributes") or {}
  for key in shared:
   val=ra.get(key)
   if val is None or str(val).strip()=="":continue
   where=key+"="+literal(val,vf[key])
   pavement_params={"f":"json","where":where,"outFields":"OBJECTID,SKEY,RKEY,PAVED,DATE_PAVED","returnGeometry":"true","outSR":4326,"resultRecordCount":30}
   paved=diagnostic_query(LAYERS["paved"],pavement_params,"PAVEMENT_LOOKUP")
   for pavement in paved:
    pg=transform(PROJECT,geom(pavement));rg=transform(PROJECT,geom(road))
    if pg.is_empty or rg.is_empty or not pg.is_valid or not rg.is_valid:continue
    envelope=transform(inverse,rg.buffer(buffer_m))
    parcel_params={"f":"json","geometry":json.dumps({"rings":[list(envelope.exterior.coords)],"spatialReference":{"wkid":4326}}),"geometryType":"esriGeometryPolygon","spatialRel":"esriSpatialRelIntersects","outFields":"OBJECTID,ACCOUNT,PKEY","returnGeometry":"true","outSR":4326,"resultRecordCount":100}
    candidates=diagnostic_query(PARCEL,parcel_params,"PARCEL_SPATIAL")
    for parcel in candidates:
     a=parcel.get("attributes") or {};pid=a.get("ACCOUNT")
     if not pid:continue
     g=transform(PROJECT,geom(parcel))
     if not g.is_valid or g.is_empty:continue
     contact=g.boundary.intersection(rg.buffer(buffer_m))
     if contact.length<=0 or g.boundary.distance(rg)>buffer_m:continue
     if g.boundary.distance(pg)>buffer_m:continue
     print("BUFFER_ADJACENT_CANDIDATE "+json.dumps({"account":str(pid),"key":key,"value":val,"road_skey":ra.get("SKEY"),"road_rkey":ra.get("RKEY"),"pavement_skey":pavement.get("attributes",{}).get("SKEY"),"pavement_rkey":pavement.get("attributes",{}).get("RKEY"),"buffer_boundary_contact_m":round(contact.length,3),"centerline_distance_m":round(g.boundary.distance(rg),3),"buffer_m":buffer_m,"legal_access_verified":False,"right_of_way_verified":False}),flush=True)
     return 0
 raise AssertionError("No buffer-adjacent candidate in first 60 centerline records")
if __name__=="__main__":sys.exit(main())
