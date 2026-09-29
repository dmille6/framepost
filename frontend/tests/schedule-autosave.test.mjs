import assert from 'node:assert/strict';
import test from 'node:test';
import { QueryClient } from '@tanstack/react-query';
import { harness, compile, elements } from './component-harness.mjs';
const { DraftAutosave } = compile('../src/lib/draftAutosave.ts');

for (const entry of ['calendar sidebar drop', 'calendar reschedule']) {
  test(`${entry} uses the shared autosave error gate`, async () => {
    const previous = globalThis.localStorage;
    globalThis.localStorage = { getItem() { return null; } };
    const qc = new QueryClient();
    try {
      let fail = true, scheduled = 0;
      const api = {
        updatePost: async id => { if (fail) throw new Error('Save is offline'); return { id, status: "pending", scheduled_at: null }; },
        schedulePost: async () => { scheduled++; return {}; },
      };
      const hooks = compile('../src/hooks/useDraftAutosaves.ts', name => {
        if (name === 'react') return { useEffect() {}, useSyncExternalStore() {} };
        if (name === '../api/client') return api;
        if (name === '../lib/draftAutosave') return { DraftAutosave };
        throw new Error(name);
      });
      const store = hooks.useDraftAutosaves(qc);
      const save = store.get('a'); save.attached = true;
      save.change('title', 'New', { title: 'New' }, { title: 'Old' });
      const imports = {
        '../hooks/usePageTitle': { usePageTitle() {} },
        '../hooks/useDraftAutosaves': { useDraftAutosaves: () => store },
      };
      for (const name of ['Calendar', 'PageHeader', 'QueueTabs', 'RescheduleSidebar', 'ScheduledList',
        'ScheduledShoots', 'ScheduleDialog', 'ScheduledItemModal', 'Topbar']) {
        imports[`../components/${name}`] = { __esModule: true, default: name };
      }
      const page = harness('../src/pages/Scheduled.tsx', { imports });
      const child = type => elements(page.render()).find(e => e.type === type);
      if (entry === 'calendar sidebar drop') {
        child('Calendar').props.onDayDrop(new Date(), 'a');
      } else {
        child('Calendar').props.onPick({ id: 'a' });
        child('ScheduledItemModal').props.onReschedule();
      }
      await assert.rejects(child('ScheduleDialog').props.onSubmit('tomorrow'), /Save is offline/);
      assert.equal(scheduled, 0);
      assert.ok(child('ScheduleDialog'));
      fail = false;
      await child('ScheduleDialog').props.onSubmit('tomorrow');
      assert.equal(scheduled, 1);
      assert.equal(child('ScheduleDialog'), undefined);
      page.unmount();
    } finally { qc.clear(); globalThis.localStorage = previous; }
  });
}

test('pending schedule blocks Cancel and backdrop until success or failure settles', async () => {
  const { button } = await import('./component-harness.mjs');
  for (const fail of [false, true]) {
    let resolve, reject, cancelled = 0;
    const page = harness('../src/components/ScheduleDialog.tsx');
    const props = { postTitle: 'Photo', onCancel: () => cancelled++, onSubmit: () => new Promise((yes, no) => { resolve = yes; reject = no; }) };
    let tree = page.render(props);
    const submitting = button(tree, 'Schedule').props.onClick();
    tree = page.render(props);
    assert.equal(button(tree, 'Cancel').props.disabled, true);
    button(tree, 'Cancel').props.onClick?.();
    tree.props.onClick?.();
    assert.equal(cancelled, 0);
    assert.ok(elements(tree).filter(e => e.type === 'input').every(e => e.props.disabled));
    if (fail) reject(new Error('Schedule failed')); else resolve();
    await submitting;
    tree = page.render(props);
    assert.equal(button(tree, 'Cancel').props.disabled, false);
    tree.props.onClick();
    assert.equal(cancelled, 1);
  }
});
