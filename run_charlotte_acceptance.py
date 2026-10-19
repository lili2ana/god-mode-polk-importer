"""Read-only live Charlotte GIS acceptance discovery. No database writes."""
import json,sys,time
import requests
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer
BASE="https://agis3.charlottecountyfl.gov/arcgis/rest/services/Essentials/"
PARCEL=BASE+"CCGIS_BaseLayers/MapServer/5"
LAYERS={"lomr":BASE+"CCGISLayers/MapServer/134","flood":BASE+"CCGISLayers/MapServer/135","wetland":BASE+"CCGISLayers/MapServer/87","paved":BASE+"CCGIS_BaseLayers/MapServer/11","road":BASE+"CCGISLayers/MapServer/113"}
NWI="https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0"
PROJECT=Transformer.from_crs("EPSG:4326","EPSG:26917",always_xy=True).transform
def query(url,**params):
    r=requests.get(url+"/query",params=dict(f="json",outSR=4326,returnGeometry="true",outFields="*",**params),timeout=25)
    r.raise_for_status();data=r.json()
    if "error" in data:raise RuntimeError(str(data["error"]))
    return data.get("features",[])
def geom(feature):
    g=feature.get("geometry") or {}
    if "rings" in g:return shape({"type":"Polygon","coordinates":g["rings"]}).buffer(0)
    if "paths" in g:return shape({"type":"MultiLineString","coordinates":g["paths"]})
    raise ValueError("Unknown geometry type")
def area(g):return transform(PROJECT,g).area
def overlap(parcel,features):
    if not parcel.is_valid or parcel.is_empty:raise ValueError("invalid parcel polygon")
    if area(parcel)<=0:raise ValueError("zero parcel area")
    pieces=[geom(f).intersection(parcel) for f in features if geom(f).intersects(parcel)]
    from shapely.ops import unary_union
    return min(100.0,max(0.0,round(100*area(unary_union(pieces))/area(parcel),4))) if pieces else 0.
def fetch_parcel(feature):
    point=geom(feature).representative_point()
    rows=query(PARCEL,geometry=json.dumps({"x":point.x,"y":point.y,"spatialReference":{"wkid":4326}}),geometryType="esriGeometryPoint",spatialRel="esriSpatialRelIntersects",resultRecordCount=3)
    return rows[0] if rows else None
def intersect(url,p):
    g=p["geometry"]
    return query(url,geometry=json.dumps({"rings":g["rings"],"spatialReference":{"wkid":4326}}),geometryType="esriGeometryPolygon",spatialRel="esriSpatialRelIntersects",resultRecordCount=100)
def scan(name,source,limit=12):
    seeds=query(source,where="1=1",resultRecordCount=limit)
    print(json.dumps({"scenario":name,"seed_count":len(seeds)}),flush=True)
    for seed in seeds:
        try:
            p=fetch_parcel(seed)
            if not p:continue
            polygon=geom(p);pid=p["attributes"].get("ACCOUNT") or p["attributes"].get("PKEY")
            if not pid:continue
            flood=intersect(LAYERS["flood"],p);lomr=intersect(LAYERS["lomr"],p)
            wet=intersect(LAYERS["wetland"],p);nwi=intersect(NWI,p)
            paved=intersect(LAYERS["paved"],p);roads=intersect(LAYERS["road"],p)
            pct={"flood":overlap(polygon,flood),"lomr":overlap(polygon,lomr),"county_wetlands":overlap(polygon,wet),"nwi_wetlands":overlap(polygon,nwi)}
            discrepancy=abs(pct["county_wetlands"]-pct["nwi_wetlands"])
            flags=["LEGAL_ACCESS_UNVERIFIED"]
            if discrepancy>=10:flags.append("WETLANDS_MATERIAL_DISCREPANCY")
            if pct["lomr"]>0:flags.append("LOMR_REVIEW")
            if any(str(f["attributes"].get("PAVED","")).strip().lower() not in ("yes","y","true","1") for f in paved):flags.append("PAVEMENT_UNVERIFIED")
            result={"scenario":name,"parcel_id":pid,"overlap_pct":pct,"wetlands_difference_pp":round(discrepancy,4),"road_count":len(roads),"pavement_count":len(paved),"flags":flags,"topology_ok":polygon.is_valid,"source":"Charlotte ArcGIS and USFWS NWI"}
            print(json.dumps(result),flush=True)
            good={"lomr":pct["lomr"]>0,"agreement":bool(wet and nwi) and discrepancy<10,"discrepancy":discrepancy>=10,"legacy":bool(roads) and "PAVEMENT_UNVERIFIED" in flags}[name]
            if good:return result
        except Exception as exc:
            print(json.dumps({"scenario":name,"candidate_error":str(exc)[:180]}),flush=True)
    return None
def main():
    results={}
    for name,source in [("lomr",LAYERS["lomr"]),("agreement",LAYERS["wetland"]),("discrepancy",LAYERS["wetland"]),("legacy",LAYERS["road"])]:
        results[name]=scan(name,source,limit=120 if name=='legacy' else 12)
    print("ACCEPTANCE_SUMMARY "+json.dumps({"found":{k:bool(v) for k,v in results.items()},"complete":all(results.values()),"production_writes_allowed":False}),flush=True)
    return 0 if all(results.values()) else 1
if __name__=="__main__":sys.exit(main())
