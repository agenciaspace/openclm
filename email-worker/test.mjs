import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import worker from './worker.mjs';

const secret = 'synthetic-mail-relay-secret-not-used-in-production';
const payload = { to: 'recipient@example.com', subject: 'Synthetic account test', text: 'Private action link' };
function request(body = payload, { signed = true, timestamp = Math.floor(Date.now() / 1000), key = secret } = {}) {
  const raw = JSON.stringify(body);
  const headers = { 'Content-Type': 'application/json' };
  if (signed) Object.assign(headers, { 'X-Mail-Timestamp': String(timestamp), 'X-Mail-Signature': createHmac('sha256', key).update(timestamp + '.' + raw).digest('hex') });
  return new Request('https://mail.example/send', { method: 'POST', headers, body: raw });
}
test('only valid signed requests reach the mail binding', async () => {
  const sent = [];
  const env = { MAIL_RELAY_SECRET: secret, MAIL_FROM_ADDRESS: 'contato@legalops.dev', EMAIL: { send: async message => { sent.push(message); } } };
  for (const options of [{ signed: false }, { key: 'wrong-secret' }, { timestamp: Math.floor(Date.now() / 1000) - 400 }]) {
    assert.equal((await worker.fetch(request(payload, options), env)).status, 401);
  }
  assert.equal(sent.length, 0);
  assert.equal((await worker.fetch(request({ ...payload, from: 'attacker@example.com' }), env)).status, 400);
  assert.equal((await worker.fetch(request({ ...payload, text: 'x'.repeat(70000) }), env)).status, 413);
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.deepEqual(sent, [{ ...payload, from: { email: 'contato@legalops.dev', name: 'OpenCLM' } }]);
});
