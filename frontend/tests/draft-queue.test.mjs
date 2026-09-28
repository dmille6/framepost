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
  const drafts = [{ id: 'blocked', title: 'In a reel', created_at: '2026-09-28' }, { id: 'free', title: 'Free', created_at: '2026-09-27' }];
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
      useMemo: fn => fn(), useEffect: () => {},
    };
    if (name === '@tanstack/react-query') return {
      useQueryClient: () => ({ invalidateQueries() {}, setQueryData() {} }),
      useQuery: ({ queryKey }) => ({ data: queryKey[0] === 'drafts' ? drafts : [] }),
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
    if (name === '../hooks/usePageTitle') return { usePageTitle() {} };
    if (name === '../lib/shoots') return { batchKey: () => null };
    if (name.startsWith('../components/')) return { __esModule: true,
      default: name.split('/').at(-1), SkeletonGrid: 'SkeletonGrid' };
    return require(name);
  }
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(load, module, module.exports);
  return {
    render: () => { cursor = 0; return module.exports.default(); },
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
