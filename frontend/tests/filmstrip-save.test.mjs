import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, elements, button, text } from './component-harness.mjs';

for (const fail of [false, true]) {
  test(`filmstrip blocks every dismissal and crop edit until the batch ${fail ? 'fails visibly' : 'succeeds'}`, async () => {
    const oldWindow = globalThis.window;
    let onKey, release, closed = 0;
    globalThis.window = { addEventListener: (_type, handler) => { onKey = handler; }, removeEventListener() {} };
    try {
      const calls = [];
      const page = harness('../src/components/IgCropFilmstrip.tsx', {
        imports: { '../hooks/useDraftAutosaves': { useDraftAutosaves: () => ({
          saveCrops: async edits => {
            calls.push(edits);
            await new Promise(resolve => { release = resolve; });
            if (fail) throw new Error('A crop changed since this filmstrip opened');
          },
        }) } },
      });
      const props = { posts: [{ id: 'a', ig_fit: null }, { id: 'b', ig_fit: 'pad_blur' }], onClose: () => closed++ };
      let tree = page.render(props);
      elements(tree).find(e => e.type === 'IgCropStudio').props.onFitChange('pad');
      tree = page.render(props);
      button(tree, 'Save 2').props.onClick();
      // Escape in the same tick is blocked, even before React renders isPending.
      onKey({ key: 'Escape' });
      tree.props.onClick();
      assert.equal(closed, 0);
      tree = page.render(props);
      for (const target of [undefined, { closest: () => ({}) }, { isContentEditable: true }]) onKey({ key: 'Escape', target });
      button(tree, 'Cancel').props.onClick();
      elements(tree).find(e => e.props['aria-label'] === 'Close').props.onClick();
      assert.equal(button(tree, 'Cancel').props.disabled, true);
      assert.equal(elements(tree).find(e => e.props['aria-label'] === 'Close').props.disabled, true);
      assert.equal(elements(tree).find(e => e.type === 'fieldset').props.disabled, true);
      elements(tree).find(e => e.type === 'IgCropStudio').props.onFitChange('pad_blur');
      assert.equal(elements(page.render(props)).find(e => e.type === 'IgCropStudio').props.fit, 'pad');
      assert.equal(closed, 0);
      assert.deepEqual(calls, [[{ post: props.posts[0], patch: { ig_fit: 'pad' } }]], 'untouched frames must not replay opening values');
      release(); await page.settle();
      tree = page.render(props);
      if (fail) {
        assert.equal(closed, 0);
        assert.match(text(tree), /A crop changed/);
        onKey({ key: 'Escape' });
      }
      assert.equal(closed, 1);
      page.unmount();
    } finally { globalThis.window = oldWindow; }
  });
}
