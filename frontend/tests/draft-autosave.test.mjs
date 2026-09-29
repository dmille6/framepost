import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/draftAutosave.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
} }).outputText;
const module = { exports: {} };
new Function('module', 'exports', compiled)(module, module.exports);
const { DraftAutosave } = module.exports;
const tick = () => new Promise(setImmediate);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function edit(save, title, before = 'Original') {
  save.change('title', title, { title }, { title: before });
}

test('mount and unchanged fields send nothing; debounce sends only changed fields', async () => {
  const sent = [];
  const save = new DraftAutosave(async patch => sent.push(patch), 15);
  await save.flush();
  edit(save, 'Original');
  await wait(25);
  assert.deepEqual(sent, []);
  edit(save, 'First');
  edit(save, 'Last');
  assert.equal(save.status, 'saving');
  await wait(25);
  assert.deepEqual(sent, [{ title: 'Last' }]);
  assert.equal(save.status, 'saved');
});

test('a response cannot clobber typing and requests for a draft never overlap', async () => {
  const first = deferred();
  const sent = [];
  const save = new DraftAutosave(async patch => {
    sent.push(patch);
    if (sent.length === 1) await first.promise;
  }, 10000);
  edit(save, 'Sent');
  const flush = save.flush();
  await tick();
  edit(save, 'Still typing', 'Sent');
  assert.equal(sent.length, 1);
  first.resolve();
  assert.equal(await flush, true);
  assert.deepEqual(sent, [{ title: 'Sent' }, { title: 'Still typing' }]);
  assert.equal(save.view.get('title'), 'Still typing');
  assert.equal(save.status, 'saved');
});

test('reverting a field during a request still restores it on the server', async () => {
  const first = deferred();
  const sent = [];
  const save = new DraftAutosave(async patch => {
    sent.push(patch);
    if (sent.length === 1) await first.promise;
  }, 10000);
  edit(save, 'Sent');
  const flush = save.flush();
  await tick();
  edit(save, 'Original', 'Sent');
  first.resolve();
  await flush;
  assert.deepEqual(sent, [{ title: 'Sent' }, { title: 'Original' }]);
});

test('failed navigation flush keeps edits and exposes a retryable error', async () => {
  const sent = [];
  let fail = true;
  const save = new DraftAutosave(async patch => {
    sent.push(patch);
    if (fail) throw new Error('Offline');
  }, 10000);
  edit(save, 'Keep this');
  assert.equal(await save.flush(), false);
  assert.equal(save.error, 'Offline');
  assert.equal(save.status, 'error');
  assert.equal(save.pending, true);
  fail = false;
  assert.equal(await save.flush(), true);
  assert.deepEqual(sent, [{ title: 'Keep this' }, { title: 'Keep this' }]);
  assert.equal(save.error, null);
});

test('an explicit flush waits for in-flight and new edits before scheduling', async () => {
  const first = deferred();
  const order = [];
  const save = new DraftAutosave(async patch => {
    order.push(patch.title);
    if (order.length === 1) await first.promise;
  }, 5);
  edit(save, 'First');
  await wait(15);
  edit(save, 'Second');
  const schedule = save.flush().then(ok => { if (ok) order.push('schedule'); });
  await tick();
  assert.deepEqual(order, ['First']);
  first.resolve();
  await schedule;
  assert.deepEqual(order, ['First', 'Second', 'schedule']);
});

test('deleting this draft cancels debounce and an unmount flush', async () => {
  const sent = [];
  const save = new DraftAutosave(async patch => sent.push(patch), 10);
  edit(save, 'Delete me');
  await save.cancel();
  assert.equal(await save.flush(), false);
  await wait(20);
  assert.deepEqual(sent, []);
});

test('deletion waits for the current PATCH but cancels later edits; failed delete resumes', async () => {
  const first = deferred();
  const sent = [];
  const save = new DraftAutosave(async patch => {
    sent.push(patch);
    if (sent.length === 1) await first.promise;
  }, 10000);
  edit(save, 'First');
  const flush = save.flush();
  await tick();
  edit(save, 'Second');
  const cancelled = save.cancel();
  first.resolve();
  await cancelled;
  assert.equal(await flush, false);
  assert.equal(sent.length, 1);
  save.resume();
  await tick();
  assert.deepEqual(sent, [{ title: 'First' }, { title: 'Second' }]);
});

test('switching drafts flushes independently and retains crop, focal and relationship changes', async () => {
  const sent = [];
  const a = new DraftAutosave(async patch => sent.push(['a', patch]), 10000);
  const b = new DraftAutosave(async patch => sent.push(['b', patch]), 10000);
  a.change('igRect', { x: 0.1 }, { ig_crop_x: 0.1, ig_crop_y: 0, ig_crop_w: 0.8, ig_crop_h: 1 },
    { ig_crop_x: null, ig_crop_y: null, ig_crop_w: null, ig_crop_h: null });
  a.change('igFocal', { x: 0.2, y: 0.4 }, { ig_focal_x: 0.2, ig_focal_y: 0.4 }, { ig_focal_x: null, ig_focal_y: null });
  a.change('igFit', 'pad', { ig_fit: 'pad' }, { ig_fit: null });
  a.change('performers', ['p'], { performer_ids: ['p'] }, { performer_ids: [] });
  const leaving = a.flush();
  edit(b, 'Other draft');
  await leaving;
  assert.equal(sent.length, 1);
  assert.equal(sent[0][1].ig_focal_x, 0.2);
  assert.equal(sent[0][1].ig_crop_w, 0.8);
  assert.equal(sent[0][1].ig_fit, 'pad');
  assert.deepEqual(sent[0][1].performer_ids, ['p']);
  await b.flush();
  assert.deepEqual(sent[1], ['b', { title: 'Other draft' }]);
});
