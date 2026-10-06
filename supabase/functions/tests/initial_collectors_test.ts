import assert from 'node:assert/strict';
import { buildHandler } from '../god-mode-dd-worker/handler.ts';
for (const broken of [false,true]) {
  const requests:string[]=[]; const patches:any[]=[];
  const row={id:'review',property_id:'property',findings:{},properties:{parcel_id:'272936880201000280',owner_name:'OWNER',acreage:1,property_type:'residential'}};
  function chain(table:string):any {
    const c:any={select:()=>c,in:()=>c,order:()=>c,limit:()=>Promise.resolve({data:table==='due_diligence_reviews'?[row]:[],error:null}),eq:()=>c,then:(resolve:any)=>resolve({data:[],error:null}),update:(p:any)=>{if(table==='due_diligence_reviews')patches.push(p);return c},insert:()=>Promise.resolve({error:null})};return c;
  }
  const sb={from:chain,rpc:async(name:string)=>({data:name==='god_mode_initial_comp_candidates'?{sample:[1,2,3].map(n=>({PARNO:String(n).padStart(18,'0'),SALE1_AMT:n*100000,SALE1_DATE:'2026-09-01',ACRES:1}))}:true,error:null})};
  const handler=buildHandler({env:(k)=>({SUPABASE_SERVICE_ROLE_KEY:'test-secret',SUPABASE_URL:'https://example.test'}[k]),createClient:()=>sb,fetch:async(url:any)=>{
    const u=String(url);requests.push(u);
    if(u.includes('wetlandsmapservice')&&broken)return new Response('{}',{status:500});
    if(u.includes('Map_Property_Appraiser'))return Response.json({features:[{attributes:{PARCELID:row.properties.parcel_id,NAME:'OWNER',AMTDUE:0},centroid:{x:-81.6,y:27.9}}]});
    if(u.includes('Map_Land_Use_and_Zoning/MapServer/9/query'))return Response.json({features:broken?[]:[{attributes:{FLUNAME:'CITY',CITY_NAME:'Lake Wales'}}]});
    if(u.includes('Polk_Roads_Map/MapServer/5/query'))return Response.json({features:[{attributes:{PrimaryName:'CAMBRIDGE WAY'}}]});
    return Response.json(u.includes('/query')?{features:[]}:{layers:[]});
  }});
  const response=await handler(new Request('https://example.test?limit=1',{method:'POST',headers:{authorization:'Bearer test-secret'}}));
  assert.equal(response.status,200);
  const calls=requests.filter(u=>u.includes('wetlandsmapservice'));
  assert.equal(calls.length,1);assert.ok(calls[0].includes('/0/query'));
  assert.equal(patches[0].findings.wetlands.hit,broken?null:false);
  assert.equal(patches[0].findings.wetlands.source_error,broken);
  assert.equal(patches[0].zoning_status,'review_required');
  assert.equal(patches[0].findings.zoning.screening_passed,false);
  assert.equal(patches[0].findings.zoning.hold_reason,broken?'zoning_no_spatial_match':'municipal_zoning_required');
  assert.equal(patches[0].access_status,'verified_near_mapped_street');
  assert.equal(patches[0].findings.comps.count,3);
  assert.equal(patches[0].findings.comps.unqualified_candidate_median,200000);
  assert.equal(patches[0].findings.comps.estimated_value,null);
  assert.ok(!requests.some(u=>u.includes('swfwmd')));
  assert.equal(patches[0].status,'review_required');
  assert.equal(patches[0].underwriting_status,'review_required');
}
console.log('PASS: one valid NWI query; empty result differs from outage; DD clearance stays held');
