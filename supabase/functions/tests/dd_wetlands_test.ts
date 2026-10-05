import assert from 'node:assert/strict';
import { buildHandler } from '../god-mode-dd-worker/handler.ts';
for (const broken of [false,true]) {
  const requests:string[]=[]; const patches:any[]=[];
  const row={id:'review',property_id:'property',findings:{},properties:{parcel_id:'272936880201000280',owner_name:'OWNER',acreage:1,property_type:'residential'}};
  function chain(table:string):any {
    const c:any={select:()=>c,in:()=>c,order:()=>c,limit:()=>Promise.resolve({data:table==='due_diligence_reviews'?[row]:[],error:null}),eq:()=>c,then:(resolve:any)=>resolve({data:[],error:null}),update:(p:any)=>{if(table==='due_diligence_reviews')patches.push(p);return c},insert:()=>Promise.resolve({error:null})};return c;
  }
  const sb={from:chain,rpc:async()=>({data:true,error:null})};
  const handler=buildHandler({env:(k)=>({SUPABASE_SERVICE_ROLE_KEY:'test-secret',SUPABASE_URL:'https://example.test'}[k]),createClient:()=>sb,fetch:async(url:any)=>{
    const u=String(url);requests.push(u);
    if(u.includes('wetlandsmapservice')&&broken)return new Response('{}',{status:500});
    if(u.includes('Map_Property_Appraiser'))return Response.json({features:[{attributes:{PARCELID:row.properties.parcel_id,NAME:'OWNER',AMTDUE:0},centroid:{x:-81.6,y:27.9}}]});
    return Response.json(u.includes('/query')?{features:[]}:{layers:[]});
  }});
  const response=await handler(new Request('https://example.test?limit=1',{method:'POST',headers:{authorization:'Bearer test-secret'}}));
  assert.equal(response.status,200);
  const calls=requests.filter(u=>u.includes('wetlandsmapservice'));
  assert.equal(calls.length,1);assert.ok(calls[0].includes('/0/query'));
  assert.equal(patches[0].findings.wetlands.hit,broken?null:false);
  assert.equal(patches[0].findings.wetlands.source_error,broken);
  assert.equal(patches[0].status,'review_required');
  assert.equal(patches[0].underwriting_status,'review_required');
}
console.log('PASS: one valid NWI query; empty result differs from outage; DD clearance stays held');
