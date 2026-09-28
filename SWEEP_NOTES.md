# FramePost correctness sweep — 28 September 2026

Base: `77754bb` on `impl/gpt-sweep`. All eleven reports remain present at this HEAD;
none was skipped as already fixed. Only this task checkout's application files were
changed. No live database, backup, platform account, or network service was used.

## Validation and delivery

- Baseline: **718 passed, 2 skipped, 1 failed**. The sole failure is the documented
  `test_write_contention.py::test_engine_sets_a_busy_timeout`, which tries to open
  `/app/data/framepost.db` in this local environment.
- Final: **760 passed, 2 skipped, 1 failed**, with the same known local failure and
  no new failures. Command: `cd backend && ../../venv/bin/python -m pytest tests -q
  -p no:cacheprovider`. Full output: `/tmp/sweep-final-backend.txt`.
- **42 new regression cases**, plus updates to the six existing status-truthfulness
  tests to reflect Flickr's archive requirement. Every item was reproduced by a
  failing regression before its fix. For item 6, the two competing-dispatch tests
  were also run with the pre-claim scheduler restored temporarily: both failed.
- Frontend: `npm ci --offline --logs-dir=/tmp/framepost-npm-logs`,
  `npx --offline tsc --noEmit`, and `npm run build` all **passed**. Offline flags
  enforce the no-network constraint. Vite emits its existing-size-style advisory
  for a bundle above 500 kB; build exits successfully.
- `git diff --check` passes. Migration upgrade/downgrade preservation is tested
  using a temporary SQLite database with foreign keys enabled.
- These notes are intentionally **not committed**.

### Commit blocker

The requested commits could not be written. `git add` failed with:

```
fatal: Unable to create '/Users/darrellmiller/Documents/code-framepost/.git/worktrees/sw-gpt/index.lock': Operation not permitted
```

The checkout is writable, but its worktree Git metadata is outside the sandbox's
writable roots. Approval policy is `never`; no attempt was made to bypass it.
The branch remains at the base commit, and all implementation/test changes remain
in the working tree. A local Git author identity is also not configured.

To preserve the requested one-item-per-commit grouping, eleven mail patches and
matching commit-message files are in `/tmp/framepost-sweep-commits/` (`01.patch`
through `11.patch`, with corresponding `.message` files). Every message carries:

```
Co-Authored-By: GPT-6-Astra <noreply@openai.com>
```

The final patches were independently checked and applied in order to a temporary
copy of the base: **all apply cleanly and reproduce every changed/new/deleted file
in this checkout exactly**. They exclude this notes file. With normal Git write
permissions and an identity configured, they can be staged one at a time using
`git apply --cached /tmp/framepost-sweep-commits/NN.patch` and committed with the
corresponding `.message` file, starting from the unchanged base index. This leaves
the final working files in place. Alternatively, use `git am` on a clean checkout
of the base. Do not apply them over these already-modified working files.

## 1. Flickr re-post cache

**Present.** `routes/posts.py::repost_to_flickr` deleted the remote photo and
cleared the Post identity, but never deleted `FlickrPhoto`. The worker's
`duplicate.find_in_flickr_cache` then found the old machine tag. Three new tests
failed: normal deletion, already-missing photo, and refused deletion.

**Fix/design.** Invalidate exactly the old photo ID after Flickr confirms deletion
(or reports photo-not-found), in the same local transaction as engagement cleanup
and requeue. A failed/unconfirmed delete returns 409 and retains the archive ID
and cache, rather than proceeding with an unknown remote state.

**Alternative.** Skipping duplicate detection or clearing by hash could admit real
duplicates or erase another photo's cache. Neither is necessary.

**Files.** `backend/routes/posts.py`, `backend/tests/test_repost_cache.py`.
**Migration.** None. **Tests.** 3 pass.
**Risks.** Remote deletion and SQLite cannot commit atomically. If local commit
fails, the retained old ID allows retry; Flickr's not-found answer completes the
same invalidation. Re-post still intentionally loses the old photo's engagement.

## 2. Archive status, Flickr chip, original retention

**Present.** `_reconcile_post_status` promoted a failed Flickr upload to posted/late
when any social row posted. `history.post_platforms` synthesized Flickr status
from that aggregate status. `purge_expired_originals` checked posted status and
thumbnail existence but never the archive ID. Four added cases failed, and the
old reconciliation tests explicitly expected the incorrect promotion.

**Fix/design.** There is **no existing partial status**: history's terminal
vocabulary is posted/late/missed/failed. Keep exhausted Flickr failures `failed`
and transient failures `pending`; successful social deliveries remain visible in
their own rows. Flickr chip success now requires `flickr_photo_id`, and deliberate
Flickr opt-outs have no spurious Flickr chip. Original purge independently requires
an archive ID when Flickr was targeted, including default/malformed target lists.
Legacy incorrectly-posted rows get truthful failed labels in history and post
responses without a destructive historical rewrite.

**Alternative.** Introducing partial would change queue filters and status semantics
throughout the app. Relying only on new reconciliation would leave legacy originals
exposed to purge, so the retention guard is independent.

**Files.** `backend/services/{delivery,scheduler,cleanup}.py`,
`backend/routes/{posts,history}.py`, `backend/tests/test_archive_retention.py`,
`backend/tests/test_post_status_truthfulness.py`.
**Migration.** None. **Tests.** 4 new + 6 existing status cases pass.
**Risks.** The archive ID is existing proof of delivery, not a fresh remote
existence check. Historical stored statuses are not rewritten; existing SQL
history filters still select by stored status, while returned labels are corrected.
Flickr opt-outs retain their original retention behavior.

## 3. Reel MP4 retention

**Present.** `purge_expired_reels` used only `created_at < cutoff` and MP4 presence,
ignoring scheduled_at, posted_at, checkpoints and the recent publish-claim columns.
Five of seven lifecycle cases failed before the change.

**Fix/design.** Retention starts at `posted_at`. Unpublished drafts and scheduled
reels are retained. Any claim timestamp/token or unresolved IG checkpoint prevents
purge. An unscheduled failed render with no checkpoint/claim counts as abandoned
and expires only after `updated_at` plus the grace period.

**Alternative.** Treating any old unscheduled ready reel as abandoned would delete
an unpublished draft without an explicit abandonment signal.

**Files.** `backend/services/cleanup.py`, `backend/tests/test_reel_retention.py`.
**Migration.** None. **Tests.** 7 pass.
**Risks.** Conservative retention may keep stale claims/checkpoints indefinitely;
resolving an ambiguous publish is preferable to destroying its video input.

## 4. Draft deletion and reel dependencies

**Present.** `delete_post` unlinked originals/thumbnails before its restrictive FK
could reject deletion. Covers have a RESTRICT FK too, in addition to reel_photos.
All three regression cases failed before the change.

**Fix/design.** Check both cover and frame dependencies, return 409 naming each reel
by caption and ID, then commit the DB deletion before unlinking any files. A later
constraint/commit failure therefore cannot destroy sources even if dependencies
change after the initial check.

**Alternative.** Cascading/removing reel membership would silently alter a reel.
Checking dependencies alone would still leave other commit failures destructive.

**Files.** `backend/routes/posts.py`, `backend/tests/test_delete_reel_dependency.py`.
**Migration.** None. **Tests.** 3 pass.
**Risks.** A post-commit unlink failure leaves an orphan for existing quarantine
cleanup. A dependency added concurrently after the friendly check can still cause
a DB constraint error, but files remain intact.

## 5. Draft save preflight

**Present.** Draft listing attached preflight, but PATCH (including an empty PATCH)
and individual GET returned bare PostOut. `MetadataEditor.tsx` computes blocked
as `post.preflight ? !post.preflight.deliverable : false`, so replacing the cached
post with the PATCH response enabled Schedule. Both regression cases failed.

**Fix/design.** A small serialization helper supplies the same preflight summary
after PATCH and individual GET. Empty edits follow the same path. Existing frontend
consumption already uses this shape and requires no editor change.

**Alternative.** Refetching after save adds another request and a temporarily
unblocked state. Sending the authoritative summary directly avoids both.

**Files.** `backend/routes/posts.py`, `backend/tests/test_draft_save_preflight.py`.
**Migration.** None. **Tests.** 2 new cases and existing preflight tests pass.
**Risks.** GET/PATCH now incur readiness checks; checks are local and reuse the
existing preflight implementation. Metadata edits can legitimately change warnings.

## 6. Competing feed jobs

**Present.** `fire_due_posts` and `retry_due_platform_posts` scanned without claiming.
The fire job updates the Post before fanout, making existing retry rows eligible
while fanout is still running. Reel claims only protected reels. Both competing
Flickr/platform dispatch regressions fail against the pre-claim scheduler.

**Fix/design.** `feed_claim` uses conditional UPDATEs with random owner tokens and
ten-minute stale leases on Post and PostPlatform. First platform attempts use
SQLite INSERT ON CONFLICT before claiming the composite key. Both dispatch paths
use the same platform attempt wrapper; already-posted/terminal/not-yet-due rows
cannot win. Claims use fresh time per attempt, not the batch scan timestamp.

Session-local before_commit guards check ownership and renew atomically with every
write. A context-local HTTP request hook renews/checks immediately before outgoing
requests, including long poll/retry loops, without holding the SQLite write lock
across HTTP. Context exit checks pending writes before removing its guard, and
release requires the owner's token. A replaced owner cannot overwrite or release
the new owner. Existing IG container/checkpoint code remains intact.

Starting a platform attempt clears its old retry timer. The stranded-row sweep
ignores live claims, conditionally rechecks eligibility when setting a new timer,
and retains the existing recovery policy: IG resumes its durable checkpoint;
ambiguous Bluesky/Pixelfed/Pinterest sends are flagged for manual review instead of
blindly duplicated. Non-IG stale-token takeover without a retry timer is also
refused atomically, including re-entry through Flickr fanout.

**Alternative.** APScheduler max_instances protects only one job name, not two jobs
or worker processes. A process-local mutex lacks durable crash ownership. Holding
a SQLite write transaction across remote HTTP would block unrelated app writes.

**Files.** `backend/services/{feed_claim,http_client,scheduler}.py`,
`backend/models.py`, `backend/alembic/versions/0040_feed_publish_claim.py`,
`backend/tests/test_feed_claims.py`.
**Migration.** `0040_feed_publish_claim`, parent `0039_reel_claim_token`; adds nullable
`publish_claimed_at` and `publish_claim_token` to posts and post_platforms. Both
upgrade and downgrade use native ALTERs (`recreate='never'`). A round-trip with FKs
enabled preserves reel_photos and posted platform history.
**Tests.** 7 new cases pass; all existing IG checkpoint, stranded-row, durability,
carousel, and reel tests pass in the full suite.
**Risks.** Requires the migration before deploying either process; stop the old
worker before migrating/restarting so unclaimed old code cannot run alongside new
code. Leases exclude competing live attempts; they cannot make an interrupted
remote API request and a SQLite commit one atomic operation. The existing IG
recovery/manual-review distinction is intentionally preserved for that ambiguity.
Lease renewal occurs on commits/HTTP requests, not in an independent heartbeat
thread; the longest ordinary single HTTP timeout is Flickr's 300s upload, below
the ten-minute lease. Exceptions still release claims; crashes leave stale leases.

## 7. Flickr auth failure policy

**Present.** `publish_errors` already had text rules for Flickr auth errors, but the
Flickr `_record_failure` path never called it. It used only FlickrError.permanent.
Structured codes were also ignored by the classifier. All three tests failed.

**Fix/design.** Classify the Flickr exception, treat numeric 98/99 as REAUTH even
if wording changes, stop that post's retries, and use existing channel_health
flagging so health/preflight request reconnecting. Timeline includes category and
human-readable reason.

**Alternative.** Marking only the adapter error permanent would stop attempts but
still fail to tell the operator to reconnect.

**Files.** `backend/services/{publish_errors,scheduler}.py`,
`backend/tests/test_flickr_auth_failure.py`.
**Migration.** None. **Tests.** 3 new + 20 classifier cases pass.
**Risks.** Existing success/reconnect behavior clears the channel flag. This does
not automatically retry terminal posts after a reconnect; existing retry controls
remain authoritative.

## 8. Unsafe one-off diagnostics

**Present.** `_rotation_test.py` immediately unlinked every matching real backup in
`/mnt/photo-data/backup`, then populated it with synthetic bytes. Inspection also
found `_diag_upload.py` sending a test upload using live Flickr credentials.
The safety regression failed before removal; neither script was executed.

**Removed.** `backend/_rotation_test.py` (destroys the real backup set) and
`backend/_diag_upload.py` (mutates the live remote archive with a diagnostic upload).

**Kept.** `_check_recent.py`, `_diag_engagement.py`, `_compare_providers.py`, and
`_diag_post.py`. They inspect data; the last creates/removes only its own diagnostic
derivatives, not original sources or backups. Provider comparison makes external
inference requests if manually run; it was not run during this sweep.

**Alternative.** Reworking the rotation script into a test is unnecessary for this
removal task; regression coverage must never invoke its destructive code.

**Files.** The two deleted scripts and
`backend/tests/test_no_destructive_diagnostics.py`.
**Migration.** None. **Tests.** 1 passes.
**Risks.** Manual diagnostic upload convenience is removed; normal upload tests
and mocked adapter tests remain available.

## 9. Backup health banner

**Present.** The frontend could compute a stale-backup reason, but immediately
returned null when health.status was ok. Backend health ignored backup age entirely;
missing last_backup also generated no frontend reason. All six test cases failed
against the original response contract/behavior.

**Fix/design.** Backend supplies backup_warnings and includes them in degraded
health. A missing/empty local backup, missing/unparseable success timestamp, or
success older than two days warns. Naive timestamps are interpreted as UTC. The
banner displays those reasons and includes them in its dismissal key so a changed
backup warning is not hidden by a prior dismissal.

**Alternative.** Changing only the banner's early return would leave health consumers
incorrectly reporting ok and duplicate stale/missing logic in two places.

**Files.** `backend/services/health.py`, `backend/tests/test_backup_health.py`,
`frontend/src/api/client.ts`, `frontend/src/components/StatusBanner.tsx`.
**Migration.** None. **Tests.** 6 pass; frontend checks/build pass.
**Risks.** Checks presence/size and saved success time, not a full SQLite integrity
scan. `scripts/push-backups.sh` writes `/opt/framepost/logs/backup-push.log`, which is
not mounted in the app containers; there is no app-visible off-box success status.
The banner makes no claim that a local backup proves off-box safety. No speculative
remote-status configuration or new external connection was introduced.

## 10. Draft queue cap

**Present.** `list_drafts` defaulted to limit 100; `listDrafts()` calls `/api/posts`
without pagination, and DraftQueue filters the returned array. A 525-draft
regression returned only 100 before the fix.

**Fix/design.** Default limit is now None (unbounded). Explicit limit/offset still
work, with positive limits capped at 500 per requested page. Add ID as a stable
secondary sort. Frontend already displays/filters all received drafts; no client
change is needed.

**Alternative.** Infinite-query pagination adds state and selection/filtering
complexity for a single-user queue with a manageable number of drafts.

**Files.** `backend/routes/posts.py`, `backend/tests/test_draft_listing.py`.
**Migration.** None. **Tests.** 1 case covers all 525 drafts and an explicit page.
**Risks.** Readiness cost and payload size grow with draft count. This is intentional
for complete visibility; explicit pagination remains available to future clients.

## 11. Import source recovery

**Present.** Import renamed the source into originals before creating the thumbnail,
then thumbnail failure unlinked that sole copy. DB failures left the source missing
from incoming. The upload route also unlinked its received file on failure. Four
regression cases failed before the changes.

**Fix/design.** Copy the source to the permanent location, retain the source until
post/event commit, and rollback a failed insert/flush/commit. After success, consume
only the same inode/size/mtime version that was copied; a replaced watch-folder file
is left alone. Failure leaves recoverable originals/artifacts; the existing orphan
sweep quarantines unowned permanent files after 24 hours. Browser error handlers
move received bytes to errors with the existing sidecar log instead of deleting them.

**Alternative.** Moving first and attempting restoration can overwrite a replacement
incoming file and makes ambiguous DB commit failures harder to recover safely.
Keeping a source until the durable record exists avoids those deletion decisions.

**Files.** `backend/services/import_pipeline.py`, `backend/routes/posts.py`,
`backend/tests/test_import_recovery.py`.
**Migration.** None. **Tests.** 5 pass: thumbnail, flush, commit, browser error,
and successful consumption after durable import.
**Risks.** Import temporarily uses space for two original copies. Failed artifacts
may occupy extra space until quarantine/review, deliberately favoring recoverability.
A failed source unlink after successful commit logs a warning; it cannot undo an
already-successful import or remove the permanent copy.

## Review fixes

The independent review of `0a3abbb` reported no High findings. All seven findings
below are fixed in the working tree. The earlier notes above describe the original
sweep; this checkout now starts at the already-created `0a3abbb` commit.

### M1 — failed archive / Instagram recovery

**Fixed.** Instagram post-now accepts a failed parent post. Fanout can claim failed
platform deliveries; the regular retry claim still accepts only pending rows.
Posted and carousel-member rows remain ineligible, and ambiguous non-Instagram
attempts still require review. A claimed failed attempt becomes pending before send
so a crash remains visible to the stranded sweep.

**Regressions:**
`test_instagram_post_now_accepts_failed_archive` and
`test_fanout_recovers_failed_platform_but_retry_claim_does_not`
(`backend/tests/test_feed_claims.py`).

### M2 — Flickr reconnect flag

**Fixed.** A successful Flickr upload clears channel health before optional
bookkeeping. A subsequent bookkeeping failure cannot restore the reconnect flag.

**Regression:**
`test_flickr_success_clears_reauth_even_when_bookkeeping_fails`
(`backend/tests/test_publish_durability.py`).

### M3 — per-platform database failures

**Fixed.** Fanout persists first-attempt destination rows before dispatch and isolates
errors around each platform, including claim acquisition, context exit/release, and
review marking. Each failure is rolled back and logged before proceeding. Unsent
new rows have a retry timer; ambiguous pending attempts remain available to the
stranded sweep and its manual-review policy. Failed rows whose acquisition fails
are requeued when the database allows it. A sustained database outage can still
prevent recovery writes; it cannot make an undurable write durable.

**Regression:**
`test_fanout_contains_database_failures_and_keeps_recovery_rows`
with `take`, `exit`, `release`, and `review` cases
(`backend/tests/test_feed_claims.py`). The acquisition case starts with no row.

### M4 — request-hook renewal failures

**Fixed.** Only claim loss aborts an outbound request. Other renewal errors are
logged and rolled back without treating them as a remote request failure. The
commit guard continues to fence a replaced owner. Renewal frequency is unchanged.

**Regression:** `test_renewal_db_failure_does_not_abort_a_live_thread`
(`backend/tests/test_feed_claims.py`), alongside the existing claim-loss fencing test.

### L1 — reel retention / regeneration race

**Fixed.** Purge snapshots each candidate's path and update timestamp, then repeats
eligibility checks in a conditional UPDATE. Active rendering is excluded. The path
clear commits before unlinking; a changed file identity/size/mtime is retained.

**Regressions:** `test_reel_purge_rechecks_candidate_before_unlink` (five cases,
including a regenerated published reel whose old publication date still qualifies)
and `test_reel_purge_commit_failure_keeps_file`
(`backend/tests/test_reel_retention.py`).

### L2 — delivery proof after claim loss

**Fixed.** Flickr and platform delivery commits catch claim loss, roll back stale
ORM changes, and persist only a narrow remote delivery record outside the ownership
guard. Existing remote identities and the successor's token are preserved. Claim
loss is then raised again, preventing follow-on bookkeeping and outbound work.
This does not eliminate the unavoidable crash window between a remote response and
a local durable write.

**Regression:** `test_lost_claim_preserves_delivery_proof_only` (Flickr and nested
post/platform claims; asserts remote identity, successor token, and rejection of
stale metadata) in `backend/tests/test_feed_claims.py`.

### L3 — concurrent reel dependency and visible deletion errors

**Fixed.** A delete-time integrity error rolls back and returns HTTP 409 before any
files are unlinked. DraftQueue displays the server's reason in a dismissible alert
using the existing danger-color error style. Bulk deletion retains failed selections;
the editor uses the mutation callback instead of leaving a rejected promise unhandled.

**Regressions:**
`test_reel_dependency_racing_delete_returns_409_and_preserves_files`
(`backend/tests/test_delete_reel_dependency.py`), plus frontend tests
`a single delete conflict is displayed as an alert` and
`bulk deletion displays conflicts and keeps only failed drafts selected`
(`frontend/tests/draft-queue.test.mjs`). The frontend tests use Node's built-in test
runner and the existing TypeScript dependency, with no new packages.

### Review validation and commit delivery

- **17 new backend regression cases; 2 frontend regression cases.**
- Full requested backend suite: **777 passed, 2 skipped, 1 failed**. The only failure
  remains `tests/test_write_contention.py::test_engine_sets_a_busy_timeout`, which
  cannot open `/app/data/framepost.db` locally. Full output:
  `/tmp/review-fixes-backend.txt`.
- `cd frontend && npx tsc --noEmit && npm run build`: **passed**; Vite retains its
  existing advisory about a bundle over 500 kB.
- `node --test frontend/tests/draft-queue.test.mjs`: **2 passed**.
- `git diff --check`: **passed**.
- No live services, accounts, or application database were used.

New commits could not be created: `git add` was rejected with
`Unable to create '/Users/darrellmiller/Documents/code-framepost/.git/worktrees/sw-gpt/index.lock': Operation not permitted`.
The worktree's Git metadata is outside the sandbox's writable roots; approval policy
is `never`. No permission bypass was attempted. HEAD remains `0a3abbb`.

Three logical patches and commit-message files are prepared in
`/tmp/framepost-review-fixes/`: `01-delivery`, `02-retention`, and `03-deletion`.
The third also records these notes. All messages include the requested trailer:

```
Co-Authored-By: GPT-6-Astra <noreply@openai.com>
```

The patches were sequentially applied to a temporary copy of their baseline files
and verified to reproduce the final working files exactly. From this already-edited
checkout, stage each patch using `git apply --cached <patch>` and commit with
`git commit -F <message>` in numeric order, with normal Git permissions and a
configured author identity. Do not apply them to the working files again.
