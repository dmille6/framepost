import assert from 'node:assert/strict';
import test from 'node:test';
import { compile } from './component-harness.mjs';

test('draft-only relationship markers reach the wire; explicit saves remain unmarked', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { cookie: '' };
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, json: async () => [] }; };
  try {
    const api = compile('../src/api/client.ts');
    for (const name of ['Albums', 'Groups', 'Profiles', 'Performers']) {
      const args = name === 'Groups' ? ['a', ['b'], false] : ['a', ['b']];
      await api[`setPost${name}`](...args, { autosave: true });
      await api[`setPost${name}`](...args);
    }
    for (let i = 0; i < calls.length; i += 2) {
      assert.match(calls[i].url, /\?autosave=true$/);
      assert.doesNotMatch(calls[i + 1].url, /autosave/);
      assert.equal(calls[i].init.method, 'PUT');
      assert.equal(calls[i].init.body, calls[i + 1].init.body);
    }
  } finally { globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});
