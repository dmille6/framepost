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
        updatePost: async id => { if (fail) throw new Error('Save is offline'); return { id }; },
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
