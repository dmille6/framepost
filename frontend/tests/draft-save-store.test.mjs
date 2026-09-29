import assert from 'node:assert/strict';
import test from 'node:test';
import { compile } from './component-harness.mjs';
const { DraftAutosave } = compile('../src/lib/draftAutosave.ts');

function setup(api, client) {
  const cache = new Map([[JSON.stringify(['drafts']), [{ id: 'a', preflight: { ready: false } }]]]);
  const invalidated = [];
  const qc = client ?? {
    cancelQueries: async () => { throw new Error("Refresh cancelled without replacement"); },
    invalidateQueries({ queryKey }) { invalidated.push(queryKey); },
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
  return { store: useDraftAutosaves(qc), cache, invalidated, unmount: () => effects.forEach(fn => fn()),
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


const tick = () => new Promise(setImmediate);
function edit(save, title) { save.change('title', title, { title }, { title: 'Original' }); }

test('a successful relationship PUT followed by a failed GET still sends a corrective reversion', async () => {
  let server = ['A'], fail = true;
  const puts = [];
  const page = setup({
    setPostPerformers: async (_id, ids) => { server = ids; puts.push(ids); },
    getPost: async id => { if (fail) throw new Error('Readiness unavailable'); return { id }; },
  });
  const save = page.store.get('a');
  save.attached = true;
  save.change('performers', ['B'], { performer_ids: ['B'] }, { performer_ids: ['A'] });
  assert.equal(await save.flush(), false);
  save.change('performers', ['A'], { performer_ids: ['A'] }, { performer_ids: ['B'] });
  assert.equal(save.status, 'error');
  assert.equal(save.pending, true);
  fail = false;
  assert.equal(await save.flush(), true);
  assert.deepEqual(puts, [['B'], ['A']]);
  assert.deepEqual(server, ['A']);
  assert.equal(save.status, 'saved');
});

test('saving B restarts the list refresh after scheduling A and never restores A', async () => {
  const { QueryClient, QueryObserver } = await import('@tanstack/react-query');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  qc.setQueryData(['drafts'], [{ id: 'a' }, { id: 'b' }]);
  const requests = [];
  const observer = new QueryObserver(qc, { queryKey: ['drafts'], queryFn: ({ signal }) =>
    new Promise(resolve => requests.push({ signal, resolve })) });
  const unsubscribe = observer.subscribe(() => {});
  const page = setup({ schedulePost: async () => ({}), updatePost: async id => ({ id, title: 'Saved B' }) }, qc);
  await page.store.schedule('a', 'tomorrow');
  assert.deepEqual(qc.getQueryData(['drafts']).map(p => p.id), ['b']);
  const refresh = requests.at(-1);
  const save = page.store.get('b');
  edit(save, 'Saved B');
  await save.flush();
  assert.equal(refresh.signal.aborted, true);
  assert.notEqual(requests.at(-1), refresh, 'a replacement refresh must start');
  refresh.resolve([{ id: 'a' }, { id: 'b' }]);
  requests.at(-1).resolve([{ id: 'b', title: 'Saved B' }]);
  await tick();
  assert.deepEqual(qc.getQueryData(['drafts']), [{ id: 'b', title: 'Saved B' }]);
  page.store.remove(['b']);
  assert.deepEqual(qc.getQueryData(['drafts']), []);
  unsubscribe(); qc.clear();
});

for (const entry of ['schedule', 'smart-fill-preview', 'smart-fill-confirm']) {
  test(`${entry} waits for in-flight and pending saves and blocks on errors`, async () => {
    const calls = [];
    let resolve, fail = false;
    const page = setup({
      updatePost: async (id, patch) => {
        calls.push(patch.title);
        if (fail) throw new Error('409: This post is no longer a draft');
        if (patch.title === 'First') await new Promise(yes => { resolve = yes; });
        return { id };
      },
      schedulePost: async () => { calls.push('schedule'); return {}; },
      smartFill: async () => { calls.push('schedule'); return { slots: [{ post_id: 'a', scheduled_at: 'tomorrow' }] }; },
    });
    const run = () => entry === 'schedule' ? page.store.schedule('a', 'tomorrow') :
      page.store.smartFill({ post_ids: ['a'], confirm: entry === 'smart-fill-confirm' });
    const save = page.store.get('a'); save.attached = true;
    edit(save, 'First');
    const pending = run();
    await tick();
    edit(save, 'Second');
    assert.deepEqual(calls, ['First']);
    resolve(); await pending;
    assert.deepEqual(calls, ['First', 'Second', 'schedule']);
    const failed = page.store.get('a'); failed.attached = true;
    edit(failed, 'Fail'); fail = true;
    await assert.rejects(run(), /no longer a draft/);
    assert.equal(calls.filter(c => c === 'schedule').length, 1);
    assert.match(failed.error, /409/);
  });
}

test('hide and pagehide flush saves; beforeunload warns for pending, in-flight and errored sessions', async () => {
  const oldWindow = globalThis.window, oldDocument = globalThis.document;
  const listeners = {};
  globalThis.window = { addEventListener: (type, handler) => { listeners[type] = handler; } };
  globalThis.document = { visibilityState: 'visible', addEventListener: window.addEventListener };
  try {
    let resolve, fail = false;
    const sent = [];
    const page = setup({ updatePost: async (id, patch) => {
      sent.push(patch.title);
      if (fail) throw new Error('Offline');
      await new Promise(yes => { resolve = yes; });
      return { id };
    } });
    const save = page.store.get('a'); save.attached = true;
    const warned = () => {
      let prevented = false;
      const event = { preventDefault() { prevented = true; } };
      listeners.beforeunload(event);
      return prevented;
    };
    assert.equal(warned(), false);
    edit(save, 'Hidden'); assert.equal(warned(), true);
    listeners.visibilitychange(); await tick(); assert.deepEqual(sent, []);
    document.visibilityState = 'hidden'; listeners.visibilitychange(); await tick();
    assert.deepEqual(sent, ['Hidden']); assert.equal(warned(), true);
    resolve(); await save.flush(); assert.equal(warned(), false);
    edit(save, 'Leaving'); listeners.pagehide(); await tick();
    assert.deepEqual(sent, ['Hidden', 'Leaving']); resolve(); await save.flush();
    fail = true; edit(save, 'Failed'); await save.flush();
    page.unmount(); await tick();
    assert.equal(warned(), true, 'failed sessions remain protected after leaving DraftQueue');
  } finally { globalThis.window = oldWindow; globalThis.document = oldDocument; }
});

test('grid subscribers receive only status or error changes while editor subscribers receive every edit', async () => {
  const page = setup({ updatePost: async id => ({ id }) });
  const save = page.store.get('a'); save.attached = true;
  let grid = 0, editor = 0;
  page.store.subscribe(() => grid++); save.subscribe(() => editor++);
  edit(save, 'A'); edit(save, 'AB'); edit(save, 'ABC');
  assert.equal(grid, 1); assert.equal(editor, 3);
  await save.flush(); assert.equal(grid, 2);
});
