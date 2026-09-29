import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, elements, text } from './component-harness.mjs';

test('calendar readiness follows server preflight, including blockers and unknown checks', () => {
  const page = harness('../src/components/RescheduleSidebar.tsx', { queries: { drafts: [
    { id: 'blocked', title: 'Title', tags: 'tags', preflight: { ready: false } },
    { id: 'ready', title: '', tags: '', preflight: { ready: true } },
    { id: 'unknown', title: 'Title', tags: 'tags' },
  ] } });
  const tree = page.render();
  const cards = elements(tree).filter(e => e.props.id);
  assert.equal(cards.find(e => e.props.id === 'ready').props.dim, undefined);
  assert.equal(cards.find(e => e.props.id === 'blocked').props.dim, true);
  assert.equal(cards.find(e => e.props.id === 'unknown').props.dim, true);
  assert.match(text(tree), /Needs attention/);
});
