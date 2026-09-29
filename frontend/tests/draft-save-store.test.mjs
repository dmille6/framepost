import assert from 'node:assert/strict';
import test from 'node:test';
import { compile } from './component-harness.mjs';
const { DraftAutosave } = compile('../src/lib/draftAutosave.ts');

function setup(api) {
  const cache = new Map([[JSON.stringify(['drafts']), [{ id: 'a', preflight: { ready: false } }]]]);
  const qc = {
    cancelQueries: async () => {}, invalidateQueries() {},
    setQueryData(key, update) {
      const k = JSON.stringify(key);
      cache.set(k, typeof update === 'function' ? update(cache.get(k)) : update);
    },
  };
  const effects = [];
  const { useDraftAutosaves } = compile('../src/hooks/useDraftAutosaves.ts', name => {
    if (name === 'react') return { useEffect: fn => { effects.push(fn()); }, useSyncExternalStore() {} };
    if (name === '../api/client') return api;
    if (name === '../lib/draftAutosave') return { DraftAutosave };
    throw new Error(`Unexpected import: ${name}`);
  });
  return { store: useDraftAutosaves(qc), cache, unmount: () => effects.forEach(fn => fn()),
    reopen: () => useDraftAutosaves(qc) };
}

test('sparse PATCH updates card preflight and an in-flight session survives route unmount', async () => {
  const calls = [];
  let resolve;
  const page = setup({ updatePost: (id, body, options) => {
    calls.push({ id, body, options });
    return new Promise(yes => { resolve = yes; });
  } });
  const save = page.store.get('a');
  save.attached = true;
  save.change('title', 'New', { title: 'New' }, { title: 'Original' });
  save.attached = false;
  page.store.release('a', save);
  page.unmount();
  await new Promise(setImmediate);
  assert.equal(page.reopen().get('a'), save);
  assert.deepEqual(calls, [{ id: 'a', body: { title: 'New' }, options: { autosave: true } }]);
  resolve({ id: 'a', title: 'New', preflight: { ready: true, deliverable: true } });
  await save.flush();
  assert.equal(page.cache.get('["drafts"]')[0].preflight.ready, true);
  assert.equal(page.store.sessions.has('a'), false);
});

test('relationship-only edits use changed endpoints and refresh readiness without an empty PATCH', async () => {
  const calls = [];
  const page = setup({
    setPostGroups: async (...args) => calls.push(['groups', ...args]),
    getPost: async id => { calls.push(['get', id]); return { id, preflight: { ready: true } }; },
    updatePost: async () => { throw new Error('Unexpected PATCH'); },
  });
  const save = page.store.get('a');
  save.attached = true;
  save.change('manualGroups', true, { use_routing: false, group_ids: ['g'] },
    { use_routing: true, group_ids: ['g'] });
  assert.equal(await save.flush(), true);
  assert.deepEqual(calls, [['groups', 'a', ['g'], false], ['get', 'a']]);
  assert.equal(page.cache.get('["drafts"]')[0].preflight.ready, true);
});
