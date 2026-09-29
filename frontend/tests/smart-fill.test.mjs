import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, elements, button } from './component-harness.mjs';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const preview = (id, time) => ({ scheduled: 1, skipped: 0, slots: [{ post_id: id, scheduled_at: time }] });

test('changes disable Schedule through debounce and refresh; superseded responses are ignored', async () => {
  const requests = [];
  const page = harness('../src/components/SmartFillDialog.tsx', { api: {
    smartFill: request => new Promise(resolve => requests.push({ request, resolve })),
  } });
  let confirmed = 0;
  const props = { postIds: ['a'], onCancel() {}, onConfirmed() { confirmed++; } };
  page.render(props);
  await wait(270);
  requests[0].resolve(preview('a', '2026-10-01T10:00:00'));
  await page.settle();
  let tree = page.render(props);
  assert.equal(button(tree, 'Schedule').props.disabled, false);
  elements(tree).find(e => e.props.type === 'time').props.onChange({ target: { value: '11:00' } });
  tree = page.render(props);
  assert.equal(button(tree, 'Refreshing preview').props.disabled, true);
  await wait(270);
  // Same number of different IDs must also supersede an in-flight proposal.
  props.postIds = ['b'];
  page.render(props);
  await wait(270);
  requests[2].resolve(preview('b', '2026-10-02T11:00:00'));
  await page.settle();
  requests[1].resolve(preview('a', '2026-10-03T11:00:00'));
  await page.settle();
  tree = page.render(props);
  button(tree, 'Schedule').props.onClick();
  assert.deepEqual(requests[3].request, {
    ...requests[2].request, confirm: true,
    slots: [{ post_id: 'b', scheduled_at: '2026-10-02T11:00:00' }],
  });
  requests[3].resolve({});
  await page.settle();
  assert.equal(confirmed, 1);
  page.unmount();
});
