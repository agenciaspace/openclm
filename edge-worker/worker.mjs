const PUBLIC = 'https://legalops.dev';
const ORIGIN = 'https://openclm.leonn.dev';
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'].includes(request.method)) return new Response('Method not allowed', {status:405});
    if (url.pathname === '/openintake' || url.pathname === '/openintake/') {
      return Response.redirect(`${PUBLIC}/openclm/intake${url.search}`, 302);
    }
    if (url.pathname === '/openclm' || url.pathname === '/openclm.html') return Response.redirect(`${PUBLIC}/openclm/${url.search}`,308);
    if (!url.pathname.startsWith('/openclm/')) return new Response('Not found',{status:404});
    const upstream = new URL(url.pathname + url.search, ORIGIN);
    const headers = new Headers(request.headers);
    headers.delete('host');
    headers.set('X-OpenCLM-Proxy','legalops.dev');
    headers.set('X-Forwarded-Host','legalops.dev');
    // Original visitor metadata is overwritten here and authenticated at the app.
    headers.set('X-OpenCLM-Client-IP',request.headers.get('CF-Connecting-IP') || '');
    headers.set('X-OpenCLM-Proxy-Key',env.PROXY_SECRET || '');
    const cookies=(headers.get('cookie') || '').split(';').map(v=>v.trim()).filter(v=>v.startsWith('openclm_session='));
    if(cookies.length) headers.set('cookie',cookies.join('; ')); else headers.delete('cookie');
    try {
      const response = await fetch(new Request(upstream,{method:request.method,headers,body:['GET','HEAD'].includes(request.method)?undefined:request.body,redirect:'manual'}));
      const outgoing=new Headers(response.headers);
      outgoing.set('Cache-Control','no-store');
      const location=outgoing.get('location');
      if(location){const target=new URL(location,upstream);if([ORIGIN,PUBLIC].includes(target.origin)){const path=target.pathname.startsWith('/openclm')?target.pathname:'/openclm'+target.pathname;outgoing.set('Location',PUBLIC+path+target.search+target.hash);}}
      return new Response(response.body,{status:response.status,headers:outgoing});
    } catch { return new Response('Serviço temporariamente indisponível.',{status:503,headers:{'Cache-Control':'no-store'}}); }
  }
};
