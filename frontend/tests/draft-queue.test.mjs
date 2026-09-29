import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/pages/DraftQueue.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText;

// Exercise the page's handlers and rendered elements without a browser or new
// dependencies. Only its hooks, API and child components are stubbed.
function harness() {
  const state = [];
  let cursor = 0;
  const pending = [];
  const effects = [];
  const calls = [];
  let drafts = [{ id: 'blocked', status: 'pending', scheduled_at: null, title: 'In a reel', created_at: '2026-09-28' }, { id: 'free', status: 'pending', scheduled_at: null, title: 'Free', created_at: '2026-09-27' }];
  const saves = {
    sessions: new Map(),
    get(id) {
      if (!this.sessions.has(id)) this.sessions.set(id, {
        view: new Map(), flush: async () => { calls.push(["flush", id]); return true; },
        cancel: async () => { calls.push(["cancel", id]); }, resume() {},
      });
      return this.sessions.get(id);
    },
    async schedule(id) { if (!await this.get(id).flush()) throw new Error("Save failed"); calls.push(["schedule", id]); this.remove([id]); },
    remove(ids) { for (const id of ids) this.sessions.delete(id); },
    async discard(id) { calls.push(["discard", id]); this.sessions.delete(id); },
    release(id, session) { calls.push(["release", id]); void session.flush(); },
  };
  const api = new Proxy({
    ApiError: Error,
    deletePost: async (id) => {
      if (id === 'blocked') throw new Error('Post is now used by a reel.');
    },
  }, { get: (target, key) => target[key] ?? (() => {}) });
  function load(name) {
    if (name === 'react') return {
      useState: (initial) => {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [state[index], value => {
          state[index] = typeof value === 'function' ? value(state[index]) : value;
        }];
      },
      useMemo: fn => fn(), useEffect: (fn, deps) => {
        const index = cursor++;
        const previous = state[index];
        if (!previous || deps.some((v, i) => !Object.is(v, previous.deps[i]))) {
          effects.push(() => { previous?.cleanup?.(); state[index] = { deps, cleanup: fn() }; });
        }
      },
    };
    if (name === '@tanstack/react-query') return {
      useQueryClient: () => ({ invalidateQueries() {}, setQueryData() {}, getQueryData: () => drafts }),
      useQuery: ({ queryKey }) => ({ data: queryKey[0] === 'drafts' ? drafts : [], isSuccess: true }),
      useMutation: options => {
        const mutateAsync = async (id) => {
          try {
            const value = await options.mutationFn(id);
            options.onSuccess?.(value, id);
            return value;
          } catch (error) { options.onError?.(error); throw error; }
        };
        return { mutateAsync, mutate: id => pending.push(mutateAsync(id).catch(() => {})) };
      },
    };
    if (name === '../api/client') return api;
    if (name === '../hooks/useDraftAutosaves') return { useDraftAutosaves: () => saves };
    if (name === '../hooks/usePageTitle') return { usePageTitle() {} };
    if (name === '../lib/shoots') return { batchKey: () => null };
    if (name.startsWith('../components/')) return { __esModule: true,
      default: name.split('/').at(-1), SkeletonGrid: 'SkeletonGrid' };
    return require(name);
  }
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(load, module, module.exports);
  return {
    render: () => { cursor = 0; const tree = module.exports.default(); effects.splice(0).forEach(fn => fn()); return tree; },
    calls, saves, drafts, setDrafts: next => { drafts = next; },
    unmount: () => state.forEach(s => s?.cleanup?.()),
    settle: async () => { await Promise.all(pending); await new Promise(setImmediate); },
  };
}
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== 'object') return [];
  return [node, ...elements(node.props?.children)];
}
function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  return typeof node === 'object' && node ? text(node.props?.children) : String(node ?? '');
}
function button(tree, label) {
  return elements(tree).find(e => e.type === 'button' && text(e).includes(label));
}

test('a single delete conflict is displayed as an alert', async () => {
  const page = harness();
  elements(page.render()).find(e => e.type === 'DraftCard').props.onDelete();
  await page.settle();
  const alert = elements(page.render()).find(e => e.props?.role === 'alert');
  assert.match(text(alert), /Post is now used by a reel/);
});

test('bulk deletion displays conflicts and keeps only failed drafts selected', async () => {
  const page = harness();
  const oldConfirm = globalThis.confirm;
  globalThis.confirm = () => true;
  try {
    button(page.render(), 'Select multiple').props.onClick();
    button(page.render(), 'Select all visible').props.onClick();
    button(page.render(), 'Delete (2)').props.onClick();
    await page.settle();
    const tree = page.render();
    assert.match(text(elements(tree).find(e => e.props?.role === 'alert')), /used by a reel/);
    assert.ok(button(tree, 'Delete (1)'));
    const cards = elements(tree).filter(e => e.type === 'DraftCard');
    assert.equal(cards.find(e => e.props.post.id === 'blocked').props.isChecked, true);
    assert.equal(cards.find(e => e.props.post.id === 'free').props.isChecked, false);
  } finally { globalThis.confirm = oldConfirm; }
});


function cards(tree) { return elements(tree).filter(e => e.type === 'DraftCard'); }
function editor(tree) { return elements(tree).find(e => e.type === 'MetadataEditor'); }

test('switch and close release the old editor and flush it without awaiting the network', () => {
  const page = harness();
  cards(page.render())[0].props.onSelect();
  page.render();
  cards(page.render())[1].props.onSelect();
  assert.equal(editor(page.render()).props.post.id, 'free');
  assert.ok(page.calls.some(([action, id]) => action === 'flush' && id === 'blocked'));
  button(page.render(), 'Close editor').props.onClick();
  assert.equal(editor(page.render()), undefined);
  assert.ok(page.calls.some(([action, id]) => action === 'flush' && id === 'free'));
});

test('delete another draft flushes the editor; delete this draft cancels its pending save', async () => {
  const page = harness();
  cards(page.render())[0].props.onSelect();
  cards(page.render())[1].props.onDelete();
  await page.settle();
  assert.ok(page.calls.some(([action, id]) => action === 'flush' && id === 'blocked'));
  editor(page.render()).props.onDelete();
  await page.settle();
  assert.ok(page.calls.some(([action, id]) => action === 'cancel' && id === 'blocked'));
});

test('save-next waits for success, uses the filtered order, and stays on failed saves', async () => {
  const page = harness();
  cards(page.render())[0].props.onSelect();
  const save = page.saves.get('blocked');
  save.flush = async () => false;
  editor(page.render()).props.onSaveNext();
  await page.settle();
  assert.equal(editor(page.render()).props.post.id, 'blocked');
  let resolve;
  save.flush = () => new Promise(yes => { resolve = yes; });
  editor(page.render()).props.onSaveNext();
  assert.equal(editor(page.render()).props.post.id, 'blocked');
  resolve(true);
  await page.settle();
  assert.equal(editor(page.render()).props.post.id, 'free');
  const input = elements(page.render()).find(e => e.type === 'input' && e.props.placeholder?.startsWith('Search'));
  input.props.onChange({ target: { value: 'Free' } });
  editor(page.render()).props.onSaveNext();
  await page.settle();
  assert.equal(editor(page.render()).props.post.id, 'free');
});

test('Schedule waits for a successful flush before opening the scheduling dialog', async () => {
  const page = harness();
  cards(page.render())[0].props.onSelect();
  const save = page.saves.get('blocked');
  let resolve;
  save.flush = () => new Promise(yes => { resolve = yes; });
  editor(page.render()).props.onSchedule();
  assert.equal(elements(page.render()).find(e => e.type === 'ScheduleDialog'), undefined);
  resolve(false);
  await page.settle();
  assert.equal(elements(page.render()).find(e => e.type === 'ScheduleDialog'), undefined);
  assert.match(text(page.render()), /Couldn't save/);
  save.flush = async () => true;
  editor(page.render()).props.onSchedule();
  await page.settle();
  assert.ok(elements(page.render()).find(e => e.type === 'ScheduleDialog'));
});


test('successful scheduling clears the editor selection before the drafts refetch resolves', async () => {
  const page = harness();
  cards(page.render())[0].props.onSelect();
  editor(page.render()).props.onSchedule(); await page.settle();
  const dialog = elements(page.render()).find(e => e.type === 'ScheduleDialog');
  await dialog.props.onSubmit('2026-10-01T10:00:00Z');
  // This harness deliberately retains the stale drafts query result.
  assert.equal(editor(page.render()), undefined);
  assert.ok(page.calls.some(([action]) => action === 'schedule'));
});

test('scheduling prunes checked IDs immediately and keeps the underlying editor inert', async () => {
  const page = harness();
  button(page.render(), 'Select multiple').props.onClick();
  button(page.render(), 'Select all visible').props.onClick();
  cards(page.render())[0].props.onSelect();
  editor(page.render()).props.onSchedule(); await page.settle();
  let tree = page.render();
  assert.equal(elements(tree).find(e => e.props.className === 'fp-page fp-fade-in').props.inert, true);
  await elements(tree).find(e => e.type === 'ScheduleDialog').props.onSubmit('tomorrow');
  tree = page.render();
  assert.ok(button(tree, 'Bulk Edit (1)'));
  assert.equal(cards(tree)[0].props.isChecked, false);
});

test('a refreshed drafts list prunes remotely scheduled and deleted checked IDs', () => {
  const page = harness();
  button(page.render(), 'Select multiple').props.onClick();
  button(page.render(), 'Select all visible').props.onClick();
  page.setDrafts([{ ...page.drafts[1], scheduled_at: 'tomorrow' }]);
  page.render();
  assert.ok(button(page.render(), 'Bulk Edit (0)'));
});

test('a remotely scheduled selected post keeps a discard banner after it leaves the draft list', async () => {
  const page = harness();
  cards(page.render())[0].props.onSelect();
  page.render();
  const save = page.saves.get('blocked');
  save.conflict = true;
  save.error = 'No longer a draft';
  page.setDrafts([page.drafts[1]]);
  let tree = page.render();
  assert.equal(editor(tree), undefined);
  const discard = button(tree, 'Post was scheduled — discard these edits');
  assert.ok(discard, 'the stale selectedId must not hide the failed session');
  discard.props.onClick(); await page.settle();
  tree = page.render();
  assert.equal(button(tree, 'Post was scheduled — discard these edits'), undefined);
  assert.ok(page.calls.some(([action, id]) => action === 'discard' && id === 'blocked'));
});
