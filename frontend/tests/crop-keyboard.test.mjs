import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, elements } from './component-harness.mjs';

test('focal marker consumes arrow keys while nudging its point', () => {
  const oldWindow = globalThis.window;
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  try {
  let point, prevented = false, stopped = false;
  const page = harness('../src/components/IgCropStudio.tsx', { api: { previewUrl: () => '', igPreviewUrl: () => '' } });
  const tree = page.render({ postId: 'a', width: 1000, height: 2000, fit: 'crop', ratioKey: '4:5',
    rect: null, focal: { x: 0.5, y: 0.5 }, onFocalChange: p => { point = p; } });
  const marker = elements(tree).find(e => e.props.role === 'slider');
  marker.props.onKeyDown({ key: 'ArrowRight', preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
  assert.deepEqual(point, { x: 0.505, y: 0.5 });
  assert.equal(prevented, true);
  assert.equal(stopped, true);
  page.unmount();
  } finally { globalThis.window = oldWindow; }
});

test('filmstrip protects text and arrow-owning controls but allows navigation from buttons and links', () => {
  const oldWindow = globalThis.window;
  let onKey;
  globalThis.window = { addEventListener: (_type, fn) => { onKey = fn; }, removeEventListener() {} };
  try {
    const page = harness('../src/components/IgCropFilmstrip.tsx');
    const props = { posts: [{ id: 'a' }, { id: 'b' }], onClose() {} };
    const current = tree => elements(tree).find(e => e.type === 'IgCropStudio').props.postId;
    page.render(props);
    for (const target of [
      { closest: selector => { assert.match(selector, /textarea/); return {}; } },
      { isContentEditable: true },
      { closest: () => ({ role: 'slider' }) },
    ]) {
      onKey({ key: 'ArrowRight', target });
      assert.equal(current(page.render(props)), 'a');
    }
    onKey({ key: 'ArrowRight', defaultPrevented: true });
    assert.equal(current(page.render(props)), 'a');
    onKey({ key: 'ArrowRight', target: { closest: () => null } });
    assert.equal(current(page.render(props)), 'b');
    onKey({ key: 'ArrowLeft', target: { closest: selector => {
      assert.ok(!selector.includes('button,') && !selector.includes('a[href]') && !selector.includes('[role],'));
      return null; // A plain button/link or a non-arrow-owning role.
    } } });
    assert.equal(current(page.render(props)), 'a');
    page.unmount();
  } finally { globalThis.window = oldWindow; }
});
