import test from 'node:test';
import assert from 'node:assert/strict';
import { createNavigationClient } from '../../src/navigationClient.js';

test('drops expired guidance, handles emergency priority, stops after disconnect', async () => {
  let clock = 0, stopped = 0, text = [], fail = false;
  const c = createNavigationClient({ base: 'http://test', now: () => clock,
    stop: () => stopped++, speak: t => text.push(t),
    fetchImpl: async (url) => {
      if (fail) throw Error('offline');
      if (url.endsWith('/sessions')) return { ok: true, json: async () => ({session_id: 's'}) };
      return { ok: true, json: async () => ({ cue: { text: 'Exit ahead', priority: 2, expires_in_ms: 1000 } }) };
    }});
  await c.start(); await c.tracking({});
  assert.deepEqual(text, ['Exit ahead']);
  c.emergency('Stop'); await c.command('where is exit');
  assert.equal(text.length, 1);
  clock = 1000; assert.equal(c.watchdog(), false);
  fail = true; assert.equal((await c.tracking({})).state, 'STOP');
  c.stop(); assert.ok(stopped > 2);
});

test('in flight requests cannot speak after cancellation', async () => {
  let finish, spoken = 0;
  const c = createNavigationClient({ base: 'http://test', speak: () => spoken++, stop() {},
    fetchImpl: async url => ({ok: true, json: () => url.endsWith('/sessions') ? {session_id:'s'} : new Promise(r => finish=r)}) });
  await c.start(); const p = c.tracking({});
  await new Promise(r => setTimeout(r, 0)); c.stop();
  finish({cue:{text:'Walk', priority:1, expires_in_ms:1000}}); await p;
  assert.equal(spoken, 0);
});
