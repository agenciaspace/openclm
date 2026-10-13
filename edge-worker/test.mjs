import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker from './worker.mjs';
test('canonical entry and authenticated original visitor metadata', async()=>{
  const redirect=await worker.fetch(new Request('https://legalops.dev/openintake'),{});
  assert.equal(redirect.status,302);assert.equal(redirect.headers.get('location'),'https://legalops.dev/openclm/intake');
  const original=globalThis.fetch;let forwarded;
  globalThis.fetch=async req=>{forwarded=req;return new Response('{"ok":true}',{headers:{'Content-Type':'application/json'}});};
  try{
    const response=await worker.fetch(new Request('https://legalops.dev/openclm/health',{headers:{'CF-Connecting-IP':'192.0.2.55','X-OpenCLM-Client-IP':'forged','X-OpenCLM-Proxy-Key':'forged','Cookie':'other=secret; openclm_session=session'}}),{PROXY_SECRET:'edge-secret'});
    assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');
    assert.equal(forwarded.headers.get('x-openclm-client-ip'),'192.0.2.55');assert.equal(forwarded.headers.get('x-openclm-proxy-key'),'edge-secret');
    assert.equal(forwarded.headers.get('cookie'),'openclm_session=session');assert.equal(new URL(forwarded.url).host,'openclm.leonn.dev');
  }finally{globalThis.fetch=original;}
});
