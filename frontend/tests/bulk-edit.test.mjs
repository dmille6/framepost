import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, elements, button, text } from './component-harness.mjs';

test('partial failures stay visible with names/errors, and retry targets only failures', async () => {
  const oldWindow = globalThis.window;
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  try {
    const updated = [];
    let fail = true, closed = 0, invalidated = [];
    const page = harness('../src/components/BulkEditDialog.tsx', {
      queries: { drafts: [{ id: 'a', title: 'First photo' }, { id: 'b', original_filename: 'second.jpg' }] },
      api: { getPost: async id => ({ id, status: "pending", scheduled_at: null }), updatePost: async (id) => {
        updated.push(id);
        if (id === 'b' && fail) throw new Error('Disk unavailable');
      } },
      queryClient: { invalidateQueries({ queryKey }) { invalidated.push(queryKey); } },
    });
    const props = { postIds: ['a', 'b'], onCancel() {}, onApplied() { closed++; } };
    let tree = page.render(props);
    elements(tree).find(e => e.type === 'input').props.onChange({ target: { value: 'New title' } });
    button(page.render(props), 'Apply to 2').props.onClick();
    await page.settle();
    tree = page.render(props);
    assert.equal(closed, 0);
    for (const id of ['a', 'b']) {
      for (const key of ['post', 'post-albums', 'post-groups', 'post-profiles', 'post-performers', 'merged-tags']) {
        assert.ok(invalidated.some(query => query[0] === key && query[1] === id), `${key} ${id}`);
      }
    }
    assert.match(text(tree), /second.jpg: Disk unavailable/);
    assert.match(text(tree), /1 of 2 drafts failed/);
    fail = false;
    button(tree, 'Retry failed').props.onClick();
    await page.settle();
    assert.deepEqual(updated, ['a', 'b', 'b']);
    assert.equal(closed, 1);
    page.unmount();
  } finally { globalThis.window = oldWindow; }
});


test('relationship caches refresh after a PUT succeeds and a later endpoint fails in the same draft', async () => {
  const oldWindow = globalThis.window;
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  try {
    const invalidated = [], calls = [];
    const page = harness('../src/components/BulkEditDialog.tsx', {
      api: {
        getPost: async id => ({ id, status: "pending", scheduled_at: null }),
        setPostAlbums: async () => { calls.push('albums saved'); },
        setPostPerformers: async () => { throw new Error('Performers unavailable'); },
        getPostPerformers: async () => [],
      },
      queryClient: { invalidateQueries: ({ queryKey }) => invalidated.push(queryKey) },
    });
    const props = { postIds: ['a'], onCancel() {}, onApplied() { throw new Error('Must stay open'); } };
    let tree = page.render(props);
    const albumToggle = elements(tree).find(e => e.props.label === 'Albums');
    // Bulk fields render a mode toggle as part of their field wrapper.
    albumToggle.props.onToggle(true);
    tree = page.render(props);
    elements(tree).find(e => e.type === 'PerformersField').props.onChange([{ id: 'p' }]);
    button(page.render(props), 'Apply to 1').props.onClick(); await page.settle();
    assert.deepEqual(calls, ['albums saved']);
    assert.match(text(page.render(props)), /Performers unavailable/);
    for (const key of ['post-albums', 'post-groups', 'post-profiles', 'post-performers']) {
      assert.ok(invalidated.some(query => query[0] === key && query[1] === 'a'));
    }
    page.unmount();
  } finally { globalThis.window = oldWindow; }
});

test('bulk edit skips stale live/deleted targets visibly and marks all remaining writes', async () => {
  const oldWindow = globalThis.window;
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  try {
    const calls = [];
    let closed = 0;
    const api = {
      getPost: async id => {
        if (id === 'deleted') throw Object.assign(new Error('Missing'), { status: 404 });
        return { id, status: 'pending', scheduled_at: id === 'live' ? 'tomorrow' : null };
      },
      getPostPerformers: async () => [],
    };
    for (const method of ['updatePost', 'setPostAlbums', 'setPostGroups', 'setPostProfiles', 'setPostPerformers']) {
      api[method] = async (...args) => {
        calls.push([method, ...args]);
        if (args[0] === 'raced') throw Object.assign(new Error('Scheduled since GET'), { status: 409 });
      };
    }
    const page = harness('../src/components/BulkEditDialog.tsx', { api });
    const props = { postIds: ['live', 'deleted', 'raced', 'draft'], onCancel() {}, onApplied() { closed++; } };
    let tree = page.render(props);
    elements(tree).find(e => e.type === 'input').props.onChange({ target: { value: 'New title' } });
    for (const label of ['Albums', 'Groups', 'Tag profiles']) {
      elements(tree).find(e => e.props.label === label).props.onToggle(true);
    }
    elements(tree).find(e => e.type === 'PerformersField').props.onChange([{ id: 'p' }]);
    button(page.render(props), 'Apply to 4').props.onClick(); await page.settle();
    assert.equal(calls.length, 6);
    assert.equal(calls.filter(call => call[1] === 'draft').length, 5);
    assert.equal(calls.filter(call => call[1] === 'raced').length, 1);
    for (const call of calls) {
      assert.ok(!['live', 'deleted'].includes(call[1]));
      assert.deepEqual(call.at(-1), { autosave: true });
    }
    assert.equal(closed, 0, 'skip note stays visible');
    assert.match(text(page.render(props)), /Skipped 3 post\(s\).*live, deleted, raced/);
    page.unmount();
  } finally { globalThis.window = oldWindow; }
});
