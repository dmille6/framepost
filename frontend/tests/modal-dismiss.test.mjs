import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, compile, elements, button, clickBackdrop } from './component-harness.mjs';

// The hooks on their own, with just enough React to run them once.
function hooks() {
  const cleanups = [];
  const react = {
    useRef: initial => ({ current: initial }),
    useEffect: fn => { cleanups.push(fn()); },
  };
  return { ...compile('../src/hooks/useModalDismiss.ts', name => {
    if (name === 'react') return react;
    throw new Error(name);
  }), cleanups };
}

function withKeys(fn) {
  const old = globalThis.window;
  const listeners = new Set();
  globalThis.window = {
    addEventListener: (_type, f) => listeners.add(f),
    removeEventListener: (_type, f) => listeners.delete(f),
  };
  const press = (key, extra = {}) => {
    const event = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
      target: { closest: () => null }, ...extra };
    [...listeners].forEach(f => f(event));
    return event;
  };
  return Promise.resolve(fn(press)).finally(() => { globalThis.window = old; });
}

test('a drag that starts in a field and ends on the backdrop does not dismiss', () => {
  const { useBackdropDismiss } = hooks();
  let dismissed = 0;
  const backdrop = {}, field = {};
  const props = useBackdropDismiss(() => dismissed++);

  // Press inside a text field, drag out, release on the backdrop: the browser sends the
  // click to the backdrop (the common ancestor). This is the stray close being fixed.
  props.onMouseDown({ target: field, currentTarget: backdrop });
  props.onClick({ target: backdrop, currentTarget: backdrop });
  assert.equal(dismissed, 0);

  // Pressed on the backdrop, released inside the card: also not a click on the backdrop.
  props.onMouseDown({ target: backdrop, currentTarget: backdrop });
  props.onClick({ target: field, currentTarget: backdrop });
  assert.equal(dismissed, 0);

  // A click with no press of its own must not inherit the previous press.
  props.onClick({ target: backdrop, currentTarget: backdrop });
  assert.equal(dismissed, 0);

  props.onMouseDown({ target: backdrop, currentTarget: backdrop });
  props.onClick({ target: backdrop, currentTarget: backdrop });
  assert.equal(dismissed, 1);
});

test('Escape reaches only the topmost dialog, and the one beneath takes over when it closes', () => withKeys(press => {
  const { useModalLayer, cleanups } = hooks();
  const closed = [];
  const lower = useModalLayer(() => closed.push('post editor'));
  const upper = useModalLayer(() => closed.push('template'));
  assert.equal(lower.isTop(), false);
  assert.equal(upper.isTop(), true);

  press('Escape');
  assert.deepEqual(closed, ['template']);

  cleanups[1]();                       // the template dialog unmounts
  assert.equal(lower.isTop(), true);
  press('Escape');
  assert.deepEqual(closed, ['template', 'post editor']);

  // A field that consumed Escape itself (the performer picker closing its list).
  press('Escape', { defaultPrevented: true });
  assert.equal(closed.length, 2);
  cleanups[0]();
}));

test('the template dialog keeps what was typed until it is applied', () => {
  const template = { id: 't1', name: 'Performance', title_template: '{performer} at {venue}',
    description_template: null, fields: [{ key: 'performer', label: 'Performer' }, { key: 'venue', label: 'Venue' }] };
  const oldStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  try {
    let applied, cancelled = 0;
    const page = harness('../src/components/ApplyTemplateDialog.tsx', { queries: { 'title-templates': [template] } });
    const props = { draftKey: 'post-1', context: { venue: 'The Venue' }, onCancel: () => cancelled++, onApply: v => { applied = v; } };
    const inputs = () => elements(page.render(props)).filter(e => e.type === 'input' && e.props.className === 'fp-input');

    assert.deepEqual(inputs().map(i => i.props.value), ['', 'The Venue']);
    inputs()[0].props.onChange({ target: { value: 'A Performer' } });
    inputs()[1].props.onChange({ target: { value: '' } });
    assert.deepEqual(inputs().map(i => i.props.value), ['A Performer', '']);

    // A drag-select that overshoots the card no longer closes it...
    const backdrop = page.render(props);
    backdrop.props.onMouseDown({ target: {}, currentTarget: backdrop });
    backdrop.props.onClick({ target: backdrop, currentTarget: backdrop });
    assert.equal(cancelled, 0);
    // ...and a deliberate close no longer costs the typing either.
    clickBackdrop(backdrop);
    assert.equal(cancelled, 1);

    page.remount();
    assert.deepEqual(inputs().map(i => i.props.value), ['A Performer', ''],
      'typed values return, including a field deliberately cleared');
    inputs()[1].props.onChange({ target: { value: 'Elsewhere' } });
    button(page.render(props), 'Apply').props.onClick();
    assert.equal(applied.title, 'A Performer at Elsewhere');

    // Applied means done: the next open starts from the post again.
    page.remount();
    assert.deepEqual(inputs().map(i => i.props.value), ['', 'The Venue']);

    // Another post never sees this one's typing.
    inputs()[0].props.onChange({ target: { value: 'Someone' } });
    page.remount();
    props.draftKey = 'post-2';
    assert.deepEqual(inputs().map(i => i.props.value), ['', 'The Venue']);
    page.unmount();
  } finally { globalThis.localStorage = oldStorage; }
});

test('a scheduled post with unsaved edits refuses to close, holds its actions, and saves before stepping', () => withKeys(async press => {
  let closed = 0, stepped = 0, saves = 0, saveWorks = true;
  const page = harness('../src/components/ScheduledItemModal.tsx', {
    queries: { 'post:a': { id: 'a', status: 'pending' } },
    imports: { '../lib/time': { absoluteTime: () => 'then' } },
  });
  const props = {
    item: { id: 'a', status: 'pending', title: 'Photo', scheduled_at: '2026-12-01T19:00:00' },
    busy: false, onClose: () => closed++, onReschedule() {}, onUnschedule: async () => {},
    onNext: () => stepped++, position: { index: 2, total: 24 },
  };
  const tree = () => page.render(props);
  const editor = () => elements(tree()).find(e => e.type === 'MetadataEditor');
  const header = () => {
    const el = elements(tree()).find(e => typeof e.type === 'function' && e.type.name === 'ModalHeader');
    return el.type(el.props);
  };
  const arrows = () => elements(tree()).filter(e => typeof e.type === 'function' && e.type.name === 'NavArrow');

  assert.equal(editor().key, 'a', 'the form is keyed to the post, so stepping starts a fresh one');
  assert.deepEqual(arrows().map(a => a.props.disabled), [true, false], 'no previous entry, one next');

  // Clean: a backdrop click closes, as before.
  clickBackdrop(tree());
  assert.equal(closed, 1);

  // Dirty: the editor reports edits and hands over its save.
  editor().props.saveRef.current = async () => { saves++; return saveWorks; };
  editor().props.onDirtyChange(true);

  clickBackdrop(tree());
  press('Escape');
  assert.equal(closed, 1, 'neither a stray click nor Escape drops the edits');
  assert.ok(button(tree(), 'Save and close'));
  press('Escape');                                   // second Escape = keep editing
  assert.equal(button(tree(), 'Save and close'), undefined);

  for (const label of ['Reschedule', 'Post now', 'Cancel schedule']) {
    assert.equal(button(header(), label).props.disabled, true, `${label} waits for a save`);
  }
  assert.ok(button(header(), 'Save'));

  // Arrow keys belong to a text field while one is focused.
  press('ArrowRight', { target: { closest: () => ({}) } });
  assert.equal(stepped, 0);

  // A failed save keeps the photographer on this post.
  saveWorks = false;
  press('ArrowRight');
  await page.settle();
  assert.deepEqual([saves, stepped], [1, 0]);

  saveWorks = true;
  press('ArrowRight');
  await page.settle();
  assert.deepEqual([saves, stepped], [2, 1], 'moving on saves first, then steps');

  // Left does nothing at the start of the queue.
  const event = press('ArrowLeft');
  assert.equal(event.defaultPrevented, false);

  // Save and close.
  clickBackdrop(tree());
  button(tree(), 'Save and close').props.onClick();
  await page.settle();
  assert.equal(closed, 2);
  page.unmount();
}));

test('previous/next walks scheduled time, then carousel order, then id', () => {
  const { navOrder, neighbours } = compile('../src/lib/scheduleNav.ts');
  const ordered = navOrder([
    { id: 'c', scheduled_at: '2026-12-02T19:00:00', carousel_position: null },
    { id: 'frame2', scheduled_at: '2026-12-01T19:00:00', carousel_position: 1 },
    { id: 'frame1', scheduled_at: '2026-12-01T19:00:00', carousel_position: 0 },
    { id: 'b', scheduled_at: '2026-11-30T19:00:00', carousel_position: null },
    { id: 'a', scheduled_at: '2026-11-30T19:00:00', carousel_position: null },
    { id: 'draft', scheduled_at: null, carousel_position: null },
  ]);
  assert.deepEqual(ordered.map(i => i.id), ['a', 'b', 'frame1', 'frame2', 'c']);
  assert.deepEqual(neighbours(ordered, 'a'), { index: 0, total: 5, prev: null, next: ordered[1] });
  assert.deepEqual(neighbours(ordered, 'c'), { index: 4, total: 5, prev: ordered[3], next: null });
  assert.deepEqual(neighbours(ordered, 'gone'), { index: -1, total: 5, prev: null, next: null });
});
