# FramePost UI fixes and Draft Queue autosave

Worktree: `impl/gpt-ui`, starting at `4c5a810`. No dependencies added.

## Delivery / Git limitation

All source edits are in this checkout. `git add` / commit cannot write the worktree's real Git metadata:

```
fatal: Unable to create '/Users/darrellmiller/Documents/code-framepost/.git/worktrees/ui-gpt/index.lock': Operation not permitted
```

The sandbox allows writing this checkout, but that metadata directory is outside its writable roots; escalation is unavailable. No commits were created. `.ui-patches/` contains six separate diffs and `commit-changes.sh`, which stages only the intended files and creates one commit per bug plus two autosave commits on the existing branch, each with `Co-Authored-By: GPT-6-Astra <noreply@openai.com>`. Run that script from this checkout in an ordinary terminal after review. The source changes are already applied; do not apply those patches again here. Neither this file nor `.ui-patches/` is staged by the script.

## Verification performed

- `cd frontend && npm ci`: passed.
- `cd frontend && npx tsc --noEmit && npm run build`: passed. Build includes the project-reference TypeScript checks. Vite reports its existing large-chunk advisory; no build failure.
- `cd frontend && node --test tests/*.test.mjs`: 26 passed. Includes the original `draft-queue.test.mjs` tests and added behavioral tests using the existing transpile/hook-harness approach. No test dependencies added.
- `cd backend && /private/tmp/claude-501/-Users-darrellmiller-Documents-code-framepost--claude-worktrees-framepost-competitor-analysis-45581b/7695a0bd-5e6d-4c06-9d4f-a2e7d379a259/scratchpad/venv/bin/python -m pytest tests -q -p no:cacheprovider`: **781 passed, 2 skipped, 1 failed**. The sole failure is the known local `test_engine_sets_a_busy_timeout`: SQLite cannot open the container-only `/app/data/framepost.db`. Four new backend tests passed.
- `git diff --check`: passed.
- Browser steps below are a human verification checklist, not a claim that browser interaction was executed. No production drafts were modified while testing.

## 1. Bulk Edit failure visibility

**Design:** The mutation returns per-draft failures, including cached title/filename and the actual error. A partial failure leaves the dialog open; Retry failed sends only failed IDs. Full success closes immediately. Refresh drafts even after partial success so closing the dialog does not leave successful cards stale. Escape is disabled while writes are running, matching the existing close controls.

**Alternative:** A toast after auto-close would still hide which operations need retry. A delayed close was rejected because it makes failures disappear.

**Files:** `frontend/src/components/BulkEditDialog.tsx`, `frontend/tests/bulk-edit.test.mjs`, shared `frontend/tests/component-harness.mjs`.

**Risks:** A draft can fail after some of its relationship writes succeeded. Retry reapplies that draft's operation; replacement PUTs and tag/performer union operations are idempotent. If fields are changed before Retry, those current values apply only to the remaining failed drafts. An unavailable draft without cached metadata falls back to its ID.

**Verified:** A two-draft mutation with one rejected PATCH remains open, names `second.jpg` and the server error, refreshes drafts, retries only that ID, and closes once it succeeds.

**Manual steps:**
1. On Draft Queue, select two disposable drafts and open Bulk Edit. Note the second draft's ID from a thumbnail/request URL in DevTools.
2. In DevTools Network request blocking, block `*/api/posts/SECOND_ID` (substitute the actual ID). Leave the drafts-list request unblocked.
3. Enter a title and Apply to 2. Wait at least one second. Confirm the dialog stays open and lists the failed draft's title/filename and error.
4. Remove the request block. Click Retry failed. Network should show an update only for the failed ID. The dialog closes after success.
5. Repeat, then close after the failure instead of retrying. The successful card should already show the new title.

## 2. Smart Fill stale preview

**Design:** Store each proposal with the immutable request that generated it. Compare serialized current parameters (including all IDs, not only their count) during render, disabling Schedule throughout debounce and refresh. An effect cleanup ignores superseded results. Confirmation uses the stored request and the exact displayed slots, with a second stale guard inside the mutation.

**Alternative:** Merely disabling while the request is pending leaves the 250 ms debounce hole. Rebuilding confirm from current inputs mixes new inputs with old slots.

**Files:** `frontend/src/components/SmartFillDialog.tsx`, `frontend/tests/smart-fill.test.mjs`.

**Risks:** A failed preview leaves Schedule disabled; changing an input requests another preview. Server scheduling validation still handles conflicts arising after preview.

**Verified:** Immediate disabling after an input change, same-count ID replacement, late superseded response rejection, and exact confirmation request/slot correspondence.

**Manual steps:**
1. Select at least two drafts and open Smart Fill. Wait until Schedule N is enabled.
2. Set DevTools network throttling to Slow 3G. Change Time of day and immediately try Schedule. It should read Refreshing preview… and be disabled before the next request starts.
3. Change Sequential / Random scatter and Skip weekends several times while requests are outstanding. Wait for the final preview.
4. Inspect the most recent preview request and its response in Network. Click Schedule N. The confirm request must carry the same parameters and displayed post/time slots, with `confirm: true`.
5. Turn throttling off when finished.

## 3. Crop marker / filmstrip keyboard ownership

**Design:** Focal-marker arrows prevent default and stop propagation. The filmstrip also ignores default-prevented events and focused native controls, links, role-based controls, and editable content (including descendants). Escape retains its existing close behavior for controls that have not consumed it.

**Alternative:** Only patching the marker leaves future custom widgets susceptible. A comprehensive filmstrip guard protects both native and custom controls.

**Files:** `frontend/src/components/IgCropStudio.tsx`, `frontend/src/components/IgCropFilmstrip.tsx`, `frontend/tests/crop-keyboard.test.mjs`.

**Risks:** An element inside a role-bearing container conservatively owns arrows. Filmstrip navigation remains available when focus is on a noninteractive surface and through its navigation buttons.

**Verified:** Real marker handler changes the focal point while consuming the event; filmstrip handler ignores consumed/control/editable events but advances on an ordinary background arrow event.

**Manual steps:**
1. Select two drafts with Instagram enabled and open the crop filmstrip.
2. Tab to the focal marker (set a focal point first if needed). Press Left/Right, then Shift+Left/Right. The marker should nudge; the current frame number/filename must stay fixed.
3. Focus the zoom range and use arrows. Zoom should change without changing frames.
4. Focus a fit/navigation button and use arrows. Filmstrip should not advance.
5. Click a noninteractive area of the filmstrip and use Left/Right. Frame navigation should still work. Escape should close.

## 4. Calendar sidebar readiness

**Design:** Use `draft.preflight?.ready ?? false`, exactly the Draft Queue readiness source. Replace the obsolete title/tags promise with delivery-check wording and rename Needs metadata to Needs attention, since delivery blockers are not necessarily missing metadata.

**Alternative:** Duplicating individual readiness checks in the browser would drift again. `deliverable` alone would also disagree with Draft Queue because readiness includes warnings.

**Files:** `frontend/src/components/RescheduleSidebar.tsx`, `frontend/tests/sidebar-readiness.test.mjs`.

**Risks:** Missing preflight is treated as unknown/not ready, matching Draft Queue. It does not prevent the server from handling an attempted drop.

**Verified:** Server-ready, server-blocked, and missing-preflight drafts group correctly even when their title/tags suggest the opposite.

**Manual steps:**
1. Identify a draft with title and tags but a real preflight issue (for example blank alt text for a warning, or a disconnected destination for a blocker).
2. Check its Draft Queue badge, then the calendar's Drag to schedule sidebar. It must be under Needs attention on the calendar.
3. Fix the issue and wait for autosave, then return to the calendar. When the server reports ready, it must appear under Ready on both screens.

## 5. Draft Queue editor autosave

**Design:** `DraftAutosave` maintains each draft's acknowledged values, latest values, and local view values. User setters enqueue normalized field changes synchronously; query hydration uses separate loaders and does not enqueue writes. An 800 ms debounce sends only changed fields. Only one write sequence per draft can run at a time, preventing out-of-order writes as well as stale responses. Edits made during a save remain pending, including a reversion to the original value.

The store belongs to the TanStack QueryClient, so it survives editor/card/route unmounts. Switching, closing, deleting another draft, and route cleanup flush without waiting in the UI. Deleting this draft cancels its timer and waits for any already-sent request before DELETE; a failed delete restores saving. Failed saves retain local view values and show an error plus Retry in the editor or a named error on the queue when another card is open. Completed, detached sessions are released so reopening uses fresh server data.

Only changed relationship selections call the existing albums/groups/profiles/performers PUT endpoints. Those writes precede PATCH (or GET for relationship-only edits), so cached readiness includes their effects. Draft cards take `preflight` directly from the response. Save responses never hydrate fields that the user has touched, preserving current typing, trailing spaces, selections, and crop/focal/fit state.

The draft editor shows Saving… / Saved / Couldn't save · Retry beside Edit draft; it has no redundant Save button. Schedule flushes before opening its dialog and again before submission, then checks the latest server preflight. Dirty edits may fix an old blocker, so stale blockers do not disable the flush action. Cmd/Ctrl+Enter flushes successfully before moving to the next draft in the captured current filtered/sorted order; it stays on the last draft or a failed save. Batch editors/Smart Fill also flush first and close the per-draft editor to avoid competing writers.

**Alternatives:** Unmount-only saves lose the debounce benefit and provide late error feedback. Parallel PATCHes with response sequence checks protect the UI but can still commit out of order on the server. A single all-fields save risks overwriting unrelated metadata or relationships that are still loading. Remounting on every response would destroy focus and typing.

**Files:** `frontend/src/lib/draftAutosave.ts`, `frontend/src/hooks/useDraftAutosaves.ts`, `frontend/src/components/MetadataEditor.tsx`, `frontend/src/pages/DraftQueue.tsx`, `frontend/src/api/client.ts`; tests in `draft-autosave.test.mjs`, `draft-save-store.test.mjs`, `metadata-autosave.test.mjs`, and `draft-queue.test.mjs`.

**Other editor audit:** `ScheduledItemModal.tsx` is MetadataEditor's only other rendered caller. It retains explicit Save and its existing dirty-state scheduling gate: silently changing a live scheduled caption/crop would be riskier than saving a local draft. `BulkEditDialog.tsx` imports MetadataEditor's template helpers but does not render it. The crop filmstrip, Bulk Edit, Find & replace, Instagram/Reddit scheduled panels, and reel editing retain their explicit action semantics. Draft autosave is opt-in via the `autosave` prop; it is not enabled globally.

**Risks / limits:** Relationship writes and PATCH are separate existing endpoints, not one atomic transaction. A partial failure retains the changes and retries idempotent writes. The save store is in memory and survives SPA navigation, not a browser crash, full reload, or forced tab/process termination; this is not an offline disk-backed editing system. Flushes on editor/route unmount do not wait for network completion. If the server is offline, return to Draft Queue and retry before reloading the page. Multi-tab or independent remote writers have no version-conflict protocol; the per-draft serialization covers this Draft Queue instance. No new confirmation dialogs were added; existing draft deletion confirmations were removed to match the local-action preference.

**Verified:** No writes on mount/hydration/unchanged values; sparse metadata/relationship saves; debouncing; edits and reversions during an outstanding request; retryable failures; crop/focal/fit persistence; old query data cannot overwrite touched fields; return to an in-flight draft; card readiness cache updates; delete cancellation and failed-delete recovery; switching/closing flushes; Schedule waits for save success; filtered save-next behavior; draft keyboard shortcut; scheduled editor retains explicit Save.

**Manual steps:**
1. Open DevTools Network and select a draft. Clear the log and wait two seconds without editing. No PATCH or relationship PUT should occur.
2. Type several characters into Title rapidly. Saving… should appear immediately; approximately 800 ms after the final keystroke, one PATCH should appear with only `title`. The URL includes `autosave=true`; the header becomes Saved.
3. Edit description, tags, performers, venue, alt text, fit, crop rectangle, and focal point. Use More fields where needed. After each save, reload/reopen only once Saved appears and confirm persistence. Text-only edits must not issue relationship PUTs.
4. Enable Slow 3G. Type a title, wait until its PATCH starts, then keep typing. The response must not revert the visible text or move the cursor. After both saves complete, reopen and confirm the last text. Repeat by changing a value and reverting it while the first PATCH is outstanding.
5. Type and immediately click another card, then immediately return. The old card's save must start without delaying navigation, and its latest text must remain visible. Repeat with Close editor and with navigating to another SPA page, then back to Draft Queue.
6. With DevTools set Offline, edit a draft and wait. Verify Couldn't save · Retry and the concrete error. Switch to another card: a named error for the failed draft must remain visible. Restore Online and click Retry. Reopen the draft and verify its text.
7. Type, then immediately delete a different disposable draft. The current draft must save. On another disposable draft, type and delete that same draft before 800 ms. Its pending PATCH must not start. With a slow already-sent PATCH, DELETE should follow it and no later PATCH should follow DELETE.
8. Use a draft referenced by a reel to provoke a rejected DELETE. Its error must be visible and the unsaved edits must remain saveable.
9. Edit a deliverable draft and immediately click Schedule. The PATCH must finish before the schedule dialog opens. Submit a future time and verify scheduling. Repeat Offline: scheduling must stop on the save error. Repeat with a genuine preflight blocker: the blocker must stop scheduling; metadata warnings alone must not.
10. Filter to one show/search and choose a sort order. Edit the first visible card and press Cmd+Enter (Mac) or Ctrl+Enter. It should save, then open the next card in that filtered order. On a failed save it must stay put. On the final card it saves without wrapping.
11. Edit a draft, then open Bulk Edit, crop filmstrip, or Smart Fill from a multi-selection. Verify the pending save finishes first and the batch tool sees the latest values.
12. Open a pending scheduled post's edit modal. Edit its title and wait two seconds: no autosave request should occur. Click Save and verify the change persists. Its reschedule action retains the explicit-save requirement while dirty.

## 6. Autosave activity coalescing

**Design:** The client marks draft PATCHes with `?autosave=true`. The PATCH handler calls `events.log_edit`, coalescing the immediately previous autosave edit event for the same post within five minutes, merging field names and updating the timestamp. It only enables coalescing for pending unscheduled posts. Explicit edits, intervening activity, a gap beyond five minutes, and live scheduled edits start new rows. Empty PATCHes still return preflight without writing an event. No schema migration is required.

**Audit:** The PATCH path writes metadata, an activity row, and recomputes preflight; it does not run AI or re-stage images. Relationship PUTs do not generate per-keystroke edited activity rows. Preflight recomputation is intentionally retained.

**Alternative:** Suppressing autosave history entirely would hide that the photographer edited a draft. Coalescing all edited rows regardless of source would obscure explicit changes to live scheduled items.

**Files:** `backend/routes/posts.py`, `backend/services/events.py`, `backend/tests/test_draft_autosave.py`, autosave option in `frontend/src/api/client.ts`.

**Risks:** A continuous editing session may keep extending one autosave row. It records the union of changed field names, not every intermediate value; that is intentional for readable activity.

**Verified:** Four backend tests cover sparse PATCH/preflight, merged field names, empty bodies, explicit/intervening events, the five-minute boundary, and exclusion of scheduled posts. Full backend suite result above.

**Manual steps:**
1. Edit a disposable draft title, wait for Saved, then edit its description and crop, waiting for Saved each time.
2. Inspect its activity/history (or the post-events response in Network). Expect one edited event with `autosave: true` and the combined field names.
3. Wait more than five minutes and edit again. Expect a new edited event.
4. Edit a scheduled item with its explicit Save button twice. Those changes should retain separate edited rows with no autosave marker.

## Review fixes

Consulted the read-only Claude reference (`../ui-claude/frontend/src/lib/autosave.ts`), particularly its treatment of failed fields and unloaded server values. This implementation retains the existing per-draft writer and marks every field in a failed operation uncertain until a successful retry acknowledges it.

| Item | Fix | Regression test name |
| --- | --- | --- |
| 1. Corrective edits after partial saves | Failed fields stay dirty even when reverted to the old baseline; a successful relationship write followed by a failed readiness read cannot produce a false Saved status. | `a successful relationship PUT followed by a failed GET still sends a corrective reversion` |
| 2. Autosaves cancelling list refreshes | Restart outstanding drafts invalidations after saves; synchronously remove scheduled/deleted drafts and restart their refresh; reject autosave PATCHes for scheduled or non-pending posts with a visible 409. | `saving B restarts the list refresh after scheduling A and never restores A`; `test_autosave_rejects_posts_that_are_no_longer_drafts` |
| 3. Scheduling bypassing saves | Draft Queue, calendar sidebar drops/rescheduling, and Smart Fill preview/confirmation share the store's flush/error gate. Pause the affected writers during scheduling; resume on failure and remove successful sessions. | `schedule waits for in-flight and pending saves and blocks on errors`; `smart-fill-preview waits for in-flight and pending saves and blocks on errors`; `smart-fill-confirm waits for in-flight and pending saves and blocks on errors`; `calendar sidebar drop uses the shared autosave error gate`; `calendar reschedule uses the shared autosave error gate` |
| 4. Partial Bulk Edit cache staleness | Invalidate post, albums, groups, profiles, performers and merged tags after each attempted draft, including partial failures while the dialog stays open. | `partial failures stay visible with names/errors, and retry targets only failures`; `relationship caches refresh after a PUT succeeds and a later endpoint fails in the same draft` |
| 5. Replacing unloaded relationships | Disable relationship controls and guard their change handlers until their per-post queries succeed, including routing overrides. Explicit Save also waits for all relationship values. | `unloaded or errored relationships cannot replace saved lists, including routing overrides` |
| 6. Reload/close/hide protection | Store-lifetime handlers flush on pagehide and hidden visibility; beforeunload prompts for pending, in-flight or failed sessions, even after leaving Draft Queue. Browser termination can still interrupt network requests; these handlers are best-effort flushes with an unload warning. | `hide and pagehide flush saves; beforeunload warns for pending, in-flight and errored sessions` |
| 7. Filmstrip arrows after button clicks | Ignore text inputs, editable content and controls whose roles own arrows; plain buttons, links and unrelated roles retain filmstrip navigation. | `filmstrip protects text and arrow-owning controls but allows navigation from buttons and links` |
| 8. Grid rerenders on every keystroke | Notify store/grid subscribers only when status or error changes; editor subscribers continue receiving every edit. | `grid subscribers receive only status or error changes while editor subscribers receive every edit` |
| 9. Selection and editor-dialog shortcuts | Clear the editor selection in schedule success immediately; ignore Cmd/Ctrl+Enter when an editor dialog is open or owns the event. | `successful scheduling clears the editor selection before the drafts refetch resolves`; `Cmd/Ctrl+Enter is ignored inside editor dialogs and while a dialog is open` |

Validation: `cd frontend && npx tsc -p tsconfig.app.json --noEmit && npm run build && node --test tests/*.test.mjs` passes (39 tests; Vite retains the large-chunk warning). The requested backend pytest command finishes with **787 passed, 2 skipped, 1 failed**. The only failure is the known local `tests/test_write_contention.py::test_engine_sets_a_busy_timeout`, whose configured SQLite database is `/app/data/framepost.db`. All six new non-draft autosave cases pass. `git diff --check` passes. No commits were made; patches and corresponding commit messages are in `../ui-fix-patches/`.

## Review fixes, round 2

All six second-round findings are addressed. Changes remain uncommitted in this checkout.

1. **Draft-only mutation guards:** Album, group, profile, and performer PUTs accept `autosave=true` and reject non-drafts with 409 before changing rows or routing flags. Autosave and Bulk Edit mark every mutation. Explicit calendar writes remain supported. Post responses now expose `scheduled_at`; a relationship-only save whose final read observes a scheduled/non-pending post remains an error with its edits retained, never Saved. Regressions: `test_relationship_autosaves_reject_non_drafts_before_mutation` (15 cases, including routing and preservation of existing rows), `test_post_response_exposes_schedule_for_relationship_only_autosave_validation`, and frontend marker/wire/non-draft response tests.
2. **Pending scheduling:** Cancel, backdrop dismissal, date, and time controls are disabled until submission settles. Draft Queue's underlying content is inert while the dialog is open. As a second defense, unexpected edits reaching a paused writer survive scheduling success with a visible unsaved-field conflict. Regressions: `pending schedule blocks Cancel and backdrop until success or failure settles`, `scheduling prunes checked IDs immediately and keeps the underlying editor inert`, and `unexpected edits during a paused schedule are retained with a visible conflict`.
3. **Stale bulk selection:** Single-post scheduling immediately removes the checked ID; deletion keeps its existing success-only pruning, and successful drafts-list updates prune remotely scheduled/deleted IDs. Smart Fill/carousel/reel completion retains its selection reset. Bulk Edit reads each target immediately before writing, skips non-drafts/deleted posts with a persistent visible note, and sends draft-only markers so an intervening schedule also rejects writes. Regressions cover selection pruning, existing partial-delete behavior, live/deleted targets, and a target scheduled between GET and mutation.
4. **Relationship refetch races:** Relationship controls and their change handlers require query success with no fetch in progress. An old successful cached value cannot enable a control while its invalidated data is being refreshed. Regression: `cached successful relationships stay locked through a slow bulk invalidation refetch` exercises all four relationship controls and routing, then verifies a subsequent edit includes the successful bulk result.
5. **Dismissible scheduled-post conflicts:** Terminal 409 sessions retain local edits and expose “Post was scheduled — discard these edits” in both the editor and detached error banner. Discard cancels the writer, removes the retained session, notifies subscribers, refreshes membership, and releases unload protection. The banner remains visible when a stale selected ID refers to a post removed by refetch. Regressions cover the editor action, store/unload cleanup, and the disappeared selected-post banner.
6. **Avoid full-list autosave refreshes:** Normal saves update the drafts/post caches from the response without invalidating the whole drafts list. If a drafts fetch is already running, it is restarted so an older snapshot cannot overwrite the save or restore a removed post. Membership-changing schedule/delete/discard paths continue to refresh. Regressions: `ordinary autosaves update cached readiness without refetching the drafts list` and the existing `saving B restarts the list refresh after scheduling A and never restores A`.

Validation: the exact frontend typecheck/build/test command passes, with **53 tests passed**. Vite retains its existing large-chunk warning. The exact backend pytest command reports **803 passed, 2 skipped, 1 failed**; the only failure is the acknowledged local `tests/test_write_contention.py::test_engine_sets_a_busy_timeout` (`sqlite3.OperationalError: unable to open database file` for the locally unavailable configured database). `git diff --check` passes. No schema migration or new dependency is needed.

Commit-ready backend and frontend patches, their plain-sentence commit messages, and application instructions are in `../ui-fix2-patches/`. Each commit message includes `Co-Authored-By: GPT-6-Astra <noreply@openai.com>`. Patches are checked by applying them in order to a clean archive of this checkout's HEAD and comparing the resulting files with the working tree. The frontend patch includes the complete previously untracked `UI_NOTES.md`, preserving its earlier sections and appending this round.
