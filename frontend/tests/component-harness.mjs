import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
export function compile(path, load = require) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(load, module, module.exports);
  return module.exports;
}

// Like draft-queue.test: execute the real component and handlers, stubbing only
// hooks, network and children. Effects run after render, with dependency cleanup.
export function harness(path, { api = {}, queries = {}, imports = {}, queryClient = {}, queryStates = {} } = {}) {
  const state = [], effects = [], pending = [];
  let cursor = 0, changed = false;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
      return [state[index], value => {
        const next = typeof value === 'function' ? value(state[index]) : value;
        if (!Object.is(next, state[index])) { state[index] = next; changed = true; }
      }];
    },
    useEffect(fn, deps) {
      const index = cursor++;
      const previous = state[index];
      if (!previous || !deps || deps.some((v, i) => !Object.is(v, previous.deps[i]))) {
        effects.push(() => {
          previous?.cleanup?.();
          state[index] = { deps, cleanup: fn() };
        });
      }
    },
    useMemo: fn => fn(),
    useCallback: fn => fn,
    useRef(initial) { return react.useState(() => ({ current: initial }))[0]; },
    useSyncExternalStore: (_subscribe, snapshot) => snapshot(),
  };
  const empty = [];
  const qc = { invalidateQueries() {}, setQueryData() {}, cancelQueries: async () => {}, isFetching: () => 0, ...queryClient };
  let autosaves;
  function load(name) {
    if (name in imports) return imports[name];
    if (name === 'react') return react;
    if (name === '@tanstack/react-query') return {
      useQueryClient: () => qc,
      useQuery: ({ queryKey }) => ({ data: queries[queryKey.join(':')] ?? queries[queryKey[0]] ?? empty, isSuccess: true, ...queryStates[queryKey[0]] }),
      useMutation: options => {
        const [busy, setBusy] = react.useState(false);
        const mutateAsync = async variables => {
          setBusy(true);
          try {
            const result = await options.mutationFn(variables);
            await options.onSuccess?.(result, variables);
            return result;
          } catch (e) { options.onError?.(e); throw e; }
          finally { setBusy(false); }
        };
        return { isPending: busy, mutateAsync,
          mutate: variables => pending.push(mutateAsync(variables).catch(() => {})) };
      },
    };
    if (name === '../hooks/useDraftAutosaves') return autosaves ??= compile('../src/hooks/useDraftAutosaves.ts', load);
    if (name === '../lib/draftAutosave') return compile('../src/lib/draftAutosave.ts');
    if (name === '../api/client') return { ApiError: Error, thumbnailUrl: id => id, ...api };
    if (name.startsWith('./')) return { __esModule: true, default: name.slice(2), ...imports[name] };
    return require(name);
  }
  const Component = compile(path, load).default;
  return {
    render(props = {}) {
      let tree, n = 0;
      do {
        changed = false; cursor = 0;
        tree = Component(props);
        effects.splice(0).forEach(fn => fn());
        if (++n > 20) throw new Error('Effect render loop');
      } while (changed);
      return tree;
    },
    async settle() { await Promise.all(pending.splice(0)); await new Promise(setImmediate); },
    unmount() { state.forEach(s => s?.cleanup?.()); },
  };
}
export function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== 'object') return [];
  return [node, ...elements(node.props?.children)];
}
export function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  return typeof node === 'object' && node ? text(node.props?.children) : String(node ?? '');
}
export function button(tree, label) {
  return elements(tree).find(e => e.type === 'button' && text(e).includes(label));
}
