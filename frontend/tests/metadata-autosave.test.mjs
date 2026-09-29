import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, compile, elements, button, text } from './component-harness.mjs';
const { DraftAutosave } = compile('../src/lib/draftAutosave.ts');

function setup(autosave, queryStates = {}) {
  const queries = {
    venues: [{ id: 'v', display_name: 'Venue' }],
    'post-albums': ['a'], 'post-groups': ['g'], 'post-profiles': ['p'],
    'post-performers': [{ id: 'p1', display_name: 'First' }],
    'connected-platforms': [{ platform: 'instagram', default_target: true }],
  };
  const page = harness('../src/components/MetadataEditor.tsx', { queries, queryStates });
  const props = { post: { id: 'one', title: 'Original', privacy: 'public', venue_id: 'v',
    groups_overridden: true, preflight: { deliverable: true, blockers: [], warnings: [] } },
    autosave, saving: false, onSave: async () => {}, onSchedule() {} };
  return { page, props, queries };
}
function titleInput(tree) { return elements(tree).find(e => e.type === 'input' && e.props.value === 'Original'); }

test('async field hydration sends nothing; autosave sends only the edited title', async () => {
  const sent = [];
  const save = new DraftAutosave(async patch => sent.push(patch), 10000);
  const { page, props } = setup(save);
  let tree = page.render(props);
  await save.flush();
  assert.deepEqual(sent, []);
  assert.equal(button(tree, 'Save'), undefined);
  titleInput(tree).props.onChange({ target: { value: 'New title' } });
  tree = page.render(props);
  assert.equal(button(tree, 'Schedule').props.disabled, false);
  assert.match(text(tree), /Saving…/);
  await save.flush();
  assert.deepEqual(sent, [{ title: 'New title' }]);
});

test('late saved metadata and refetched performers do not overwrite new input', async () => {
  let resolve;
  const save = new DraftAutosave(() => new Promise(yes => { resolve = yes; }), 10000);
  const { page, props, queries } = setup(save);
  titleInput(page.render(props)).props.onChange({ target: { value: 'Sent' } });
  const saving = save.flush();
  await new Promise(setImmediate);
  let tree = page.render(props);
  elements(tree).find(e => e.type === 'input' && e.props.value === 'Sent').props.onChange({ target: { value: 'Still typing ' } });
  elements(tree).find(e => e.type === 'PerformersField').props.onChange([{ id: 'p2', display_name: 'Second' }]);
  // Simulate both the PATCH response and relationship refetch from the older request.
  props.post = { ...props.post, title: 'Sent' };
  queries['post-performers'] = [{ id: 'p3', display_name: 'Old server response' }];
  tree = page.render(props);
  assert.ok(elements(tree).find(e => e.type === 'input' && e.props.value === 'Still typing '));
  assert.equal(elements(tree).find(e => e.type === 'PerformersField').props.selected[0].id, 'p2');
  const cancel = save.cancel();
  resolve();
  await cancel; await saving;
});

test('reopening an in-flight or failed draft restores local text and crop state', async () => {
  const save = new DraftAutosave(async () => { throw new Error('Offline'); }, 10000);
  const first = setup(save);
  let tree = first.page.render(first.props);
  titleInput(tree).props.onChange({ target: { value: 'Keep spaces ' } });
  const crop = elements(tree).find(e => e.type === 'IgCropStudio');
  crop.props.onFitChange('pad');
  crop.props.onFocalChange({ x: 0.2, y: 0.7 });
  await save.flush();
  first.page.unmount();
  const second = setup(save);
  tree = second.page.render(second.props);
  assert.ok(elements(tree).find(e => e.type === 'input' && e.props.value === 'Keep spaces '));
  const restored = elements(tree).find(e => e.type === 'IgCropStudio');
  assert.equal(restored.props.fit, 'pad');
  assert.deepEqual(restored.props.focal, { x: 0.2, y: 0.7 });
  assert.match(text(tree), /Couldn't save/);
  assert.match(text(tree), /Offline/);
});

test('scheduled editor keeps explicit Save and does not autosave; shortcut is draft-only', () => {
  const { page, props } = setup(undefined);
  let calls = 0;
  props.onSave = async () => { calls++; };
  let tree = page.render(props);
  titleInput(tree).props.onChange({ target: { value: 'Review first' } });
  tree = page.render(props);
  assert.equal(calls, 0);
  assert.equal(button(tree, 'Schedule').props.disabled, true);
  button(tree, 'Save').props.onClick();
  assert.equal(calls, 1);
  assert.match(text(tree), /Save before scheduling/);
});

test('Cmd/Ctrl+Enter invokes save-next, and a changed crop carries its authored ratio', async () => {
  const sent = [];
  const save = new DraftAutosave(async patch => sent.push(patch), 10000);
  const { page, props, queries } = setup(save);
  queries.config = { ig_min_ratio_support: '3:4' };
  props.post = { ...props.post, ig_crop_x: 0, ig_crop_y: 0, ig_crop_w: 0.8, ig_crop_h: 1, ig_crop_ratio: '4:5' };
  let next = 0;
  props.onSaveNext = () => { next++; };
  const tree = page.render(props);
  tree.props.onKeyDown({ key: 'Enter', metaKey: true, preventDefault() {} });
  tree.props.onKeyDown({ key: 'Enter', ctrlKey: true, preventDefault() {} });
  assert.equal(next, 2);
  elements(tree).find(e => e.type === 'IgCropStudio').props.onRectChange({ x: 0.1, y: 0, w: 0.8, h: 1 });
  await save.flush();
  assert.deepEqual(sent, [{ ig_crop_x: 0.1, ig_crop_ratio: '3:4' }]);
});


test('unloaded or errored relationships cannot replace saved lists, including routing overrides', async () => {
  const sent = [];
  const save = new DraftAutosave(async patch => sent.push(patch), 10000);
  const states = Object.fromEntries(['albums', 'groups', 'profiles', 'performers'].map(key =>
    [`post-${key}`, { data: undefined, isSuccess: false }]));
  const { page, props } = setup(save, states);
  button(page.render(props), 'More fields').props.onClick();
  for (const isError of [false, true]) {
    for (const state of Object.values(states)) state.isError = isError;
    const tree = page.render(props);
    assert.equal(elements(tree).filter(e => e.type === 'fieldset' && e.props.disabled).length, 4);
    elements(tree).find(e => e.type === 'PerformersField').props.onChange([{ id: 'new' }]);
    for (const chips of elements(tree).filter(e => e.type === 'MultiSelectChips')) chips.props.onChange(new Set(['new']));
    for (const routing of elements(tree).filter(e => e.type === 'RoutedGroups')) routing.props.onOverride();
    await save.flush(); assert.deepEqual(sent, []);
  }
  for (const state of Object.values(states)) { delete state.data; state.isSuccess = true; state.isError = false; }
  const tree = page.render(props);
  assert.equal(elements(tree).filter(e => e.type === 'fieldset' && e.props.disabled).length, 0);
  const performers = elements(tree).find(e => e.type === 'PerformersField');
  assert.equal(performers.props.selected[0].id, 'p1');
  performers.props.onChange([...performers.props.selected, { id: 'new' }]);
  await save.flush(); assert.deepEqual(sent, [{ performer_ids: ['p1', 'new'] }]);
});

test('Cmd/Ctrl+Enter is ignored inside editor dialogs and while a dialog is open', () => {
  const save = new DraftAutosave(async () => {}, 10000);
  const { page, props } = setup(save);
  let next = 0; props.onSaveNext = () => next++;
  let tree = page.render(props);
  for (const modifier of ['metaKey', 'ctrlKey']) {
    tree.props.onKeyDown({ key: 'Enter', [modifier]: true, target: { closest: () => ({}) }, preventDefault() {} });
  }
  assert.equal(next, 0);
  const templateButton = elements(tree).find(e => e.props.label === 'Title').props.hint;
  templateButton.props.onClick({ preventDefault() {} });
  tree = page.render(props);
  tree.props.onKeyDown({ key: 'Enter', metaKey: true, preventDefault() {} });
  assert.equal(next, 0);
});

test('cached successful relationships stay locked through a slow bulk invalidation refetch', async () => {
  const sent = [];
  const save = new DraftAutosave(async patch => sent.push(patch), 10000);
  const states = Object.fromEntries(['albums', 'groups', 'profiles', 'performers'].map(key =>
    [`post-${key}`, { isSuccess: true, isFetching: true }]));
  const { page, props, queries } = setup(save, states);
  button(page.render(props), 'More fields').props.onClick();
  let tree = page.render(props);
  assert.equal(elements(tree).filter(e => e.type === 'fieldset' && e.props.disabled).length, 4);
  elements(tree).find(e => e.type === 'PerformersField').props.onChange([{ id: 'stale' }]);
  for (const chips of elements(tree).filter(e => e.type === 'MultiSelectChips')) chips.props.onChange(new Set(['stale']));
  for (const routing of elements(tree).filter(e => e.type === 'RoutedGroups')) routing.props.onOverride();
  await save.flush(); assert.deepEqual(sent, []);
  queries['post-performers'] = [{ id: 'bulk', display_name: 'Bulk result' }];
  for (const state of Object.values(states)) state.isFetching = false;
  tree = page.render(props);
  const performers = elements(tree).find(e => e.type === 'PerformersField');
  assert.equal(performers.props.selected[0].id, 'bulk');
  performers.props.onChange([...performers.props.selected, { id: 'new' }]);
  await save.flush();
  assert.deepEqual(sent, [{ performer_ids: ['bulk', 'new'] }]);
});

test('a scheduled-post conflict offers an explicit discard action in the editor', async () => {
  const save = new DraftAutosave(async () => { throw Object.assign(new Error('No longer a draft'), { status: 409 }); }, 10000);
  const { page, props } = setup(save);
  let discarded = 0;
  props.onDiscardEdits = () => discarded++;
  titleInput(page.render(props)).props.onChange({ target: { value: 'Unsaved' } });
  await save.flush();
  const tree = page.render(props);
  assert.equal(button(tree, 'Retry'), undefined);
  button(tree, 'Post was scheduled — discard these edits').props.onClick();
  assert.equal(discarded, 1);
});
