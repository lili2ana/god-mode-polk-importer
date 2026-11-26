import { assertDbWritesAuthorized, WriteAuthorizationDenied } from "../_shared/write_guard.ts";
import { createClient } from "npm:@supabase/supabase-js@2.57.0";
import { parcelSource as source, parcelGeometry, runScreens } from "./screen.mjs";
const respond=(v:unknown,s=200)=>new Response(JSON.stringify(v),{status:s,headers:{"content-type":"application/json"}});
export async function handleRequest(req: Request, getEnv: (name: string) => string | undefined = (name) => Deno.env.get(name)): Promise<Response> {
 if(req.method==="POST") {
  try { assertDbWritesAuthorized(getEnv); }
  catch(error) { if(error instanceof WriteAuthorizationDenied) return respond({error:error.code},403); throw error; }
 }
 if(req.method!=="POST")return respond({error:"POST required"},405);
 const url=Deno.env.get("SUPABASE_URL"),key=Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
 if(!url||!key)return respond({error:"server configuration missing"},503);
 const auth=req.headers.get("authorization")||"";
 if(!auth.startsWith("Bearer "))return respond({error:"authentication required"},401);
 const client=createClient(url,key,{auth:{persistSession:false}});
 const token=auth.slice(7);
 const {data:user,error:authError}=await client.auth.getUser(token);
 if(authError||!user?.user)return respond({error:"valid operator session required"},401);
 // Privileged role is intentionally required; only server-side approved operator may invoke.
 const approved=(user.user.app_metadata?.role==="operator"||user.user.app_metadata?.role==="admin"||user.user.app_metadata?.app_role==="owner");
 if(!approved)return respond({error:"operator authorization required"},403);
 let body:any;try{body=await req.json()}catch{return respond({error:"invalid JSON"},400)}
 const parcel=String(body?.parcel_id||"");
 if(!/^[A-Za-z0-9]{12,25}$/.test(parcel))return respond({error:"invalid parcel id"},400);
 const {data:candidate,error:candidateError}=await client.rpc("god_mode_gis_candidate",{p_parcel_id:parcel});
 if(candidateError)return respond({error:"private queue RPC unavailable",detail:candidateError.message},503);
 if(!candidate)return respond({error:"parcel not in pending queue"},404);
 if(String(candidate.county).toLowerCase()!=="lee")return respond({error:"county integration not available"},422);
 const query=new URL(source);query.searchParams.set("where","STRAP = '"+parcel+"'");query.searchParams.set("outFields","OBJECTID,STRAP,FOLIOID");query.searchParams.set("returnGeometry","true");query.searchParams.set("outSR","4326");query.searchParams.set("f","json");
 try{
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),12000);
 let res:Response;try{res=await fetch(query,{signal:controller.signal})}finally{clearTimeout(timer)}
 if(!res.ok)throw new Error("GIS_HTTP_"+res.status);
 const data=await res.json();if(data.error)throw new Error("GIS_QUERY_ERROR");
 const geometry=parcelGeometry(data,parcel);
 const features=data.features;
 const matched=features.filter((f:any)=>f?.attributes?.STRAP===parcel&&Array.isArray(f?.geometry?.rings));
 const result=matched.length===1?"FOUND":"UNVERIFIED";
 const details={numeric_parcel_objectid:features[0]?.attributes?.OBJECTID,folioid:features[0]?.attributes?.FOLIOID,feature_count:features.length,geometry_found:matched.length===1,area_acres:matched[0]?.attributes?.GISACRES??null,building_count:matched[0]?.attributes?.BLDGCOUNT??null,spatial_reference:4326,checks_remaining:["FEMA_FLOOD","USFWS_WETLANDS","LEE_ROAD"],note:"Geometry alone cannot establish flood/wetlands/paved-road pass"};
 const {error:writeError}=await client.rpc("god_mode_gis_record",{p_parcel_id:parcel,p_source:"LEE_PARCEL_GEOMETRY",p_url:query.toString(),p_result:result,p_details:details});
 if(writeError)return respond({error:"evidence persistence failed",detail:writeError.message},502);
 const checks=await runScreens(geometry,fetch,async(source,url,result,details)=>{
 const {error}=await client.rpc("god_mode_gis_record",{p_parcel_id:parcel,p_source:source,p_url:url,p_result:result,p_details:details});
 if(error)throw new Error("evidence persistence failed");
 });
 return respond({parcel_id:parcel,checks,geometry_status:result,evidence_saved:true,qualification_status:"HOLD_PENDING_EXTERNAL_EVIDENCE",details});
 }catch(e){return respond({parcel_id:parcel,error:"GIS retrieval failed",detail:String(e),qualification_status:"HOLD"},502)}
}

if (import.meta.main) Deno.serve((req: Request) => handleRequest(req));
