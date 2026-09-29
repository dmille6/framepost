import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, elements, button, text } from './component-harness.mjs';

test('partial failures stay visible with names/errors, and retry targets only failures', async () => {
  const oldWindow = globalThis.window;
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  try {
    const updated = [];
    let fail = true, closed = 0, invalidated = 0;
    const page = harness('../src/components/BulkEditDialog.tsx', {
      queries: { drafts: [{ id: 'a', title: 'First photo' }, { id: 'b', original_filename: 'second.jpg' }] },
      api: { updatePost: async (id) => {
        updated.push(id);
        if (id === 'b' && fail) throw new Error('Disk unavailable');
      } },
      queryClient: { invalidateQueries() { invalidated++; } },
    });
    const props = { postIds: ['a', 'b'], onCancel() {}, onApplied() { closed++; } };
    let tree = page.render(props);
    elements(tree).find(e => e.type === 'input').props.onChange({ target: { value: 'New title' } });
    button(page.render(props), 'Apply to 2').props.onClick();
    await page.settle();
    tree = page.render(props);
    assert.equal(closed, 0);
    assert.equal(invalidated, 1);
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
