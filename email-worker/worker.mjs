const encoder = new TextEncoder();
const json = (body, status = 200) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === '/health' && request.method === 'GET') return json({ status: env.MAIL_RELAY_SECRET && env.EMAIL ? 'ok' : 'unconfigured' });
    if (path !== '/send' || request.method !== 'POST') return json({ error: 'Not found' }, 404);
    const timestamp = request.headers.get('X-Mail-Timestamp') || '';
    const signature = request.headers.get('X-Mail-Signature') || '';
    if (!env.MAIL_RELAY_SECRET || !/^\d{10}$/.test(timestamp) || Math.abs(Date.now() / 1000 - Number(timestamp)) > 300 || !/^[0-9a-f]{64}$/.test(signature)) return json({ error: 'Unauthorized' }, 401);
    if (Number(request.headers.get('Content-Length')) > 65536 || !request.body) return json({ error: 'Payload too large' }, 413);
    const reader = request.body.getReader();
    const chunks = []; let size = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 65536) { await reader.cancel(); return json({ error: 'Payload too large' }, 413); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const raw = new TextDecoder().decode(bytes);
    const key = await crypto.subtle.importKey('raw', encoder.encode(env.MAIL_RELAY_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
    const supplied = Uint8Array.from(signature.match(/../g), byte => parseInt(byte, 16));
    if (!await crypto.subtle.verify('HMAC', key, supplied, encoder.encode(timestamp + '.' + raw))) return json({ error: 'Unauthorized' }, 401);
    let body;
    try { body = JSON.parse(raw); } catch { return json({ error: 'Invalid message' }, 400); }
    if (!body || typeof body.to !== 'string' || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.to) || typeof body.subject !== 'string' || body.subject.length > 300 || /[\r\n]/.test(body.subject) || typeof body.text !== 'string' || body.text.length > 32000 || Object.keys(body).some(key => !['to', 'subject', 'text'].includes(key))) return json({ error: 'Invalid message' }, 400);
    try {
      await env.EMAIL.send({ from: { email: env.MAIL_FROM_ADDRESS, name: 'OpenCLM' }, to: body.to, subject: body.subject, text: body.text });
      return json({ accepted: true });
    } catch {
      // Provider errors can contain recipient and message details. Never log them.
      console.warn(JSON.stringify({ event: 'transactional_email_failed' }));
      return json({ error: 'Delivery temporarily unavailable' }, 503);
    }
  }
};
