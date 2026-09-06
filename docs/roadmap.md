# FramePost — Roadmap

Deferred work, with enough context to pick any item up cold. Effort estimates are
evening-sized: half a day means one sitting.

Ordered by value within each section, not by date added.

---

## Next up

### CI gate
**½–1 day.** One command — backend tests, the route-inventory check, `tsc --noEmit`,
a production build, and a migration smoke test on an empty DB — wired to run on every
push. The tests exist; nothing makes them run. This is the highest-leverage item on the
list precisely because it isn't a feature.

### Off-host encrypted backups + one restore drill
**½–1½ days.** Nightly backups currently land on the machine they're backing up, which
protects against a bad migration and nothing else. restic or borg to anywhere else.
The drill matters more than the backup — an untested restore isn't a backup.

### Pre-flight validation at schedule time
**2–4 days.** Validate a post when it's *scheduled*, not when it fires: caption length,
aspect ratio, missing Pinterest board, absent alt text. Adapters expose
`validate_post(post) -> list[Issue]`, surfaced through the ready-to-schedule checklist
that already exists.

This matters far more here than in the tools it's borrowed from. Postiz validates at
create time and its window between mistake and discovery is minutes. With a 12-month
scatter, ours is seasons — an invalid post sits quietly until 9pm on a Tuesday in March.

---

## Instagram carousels (multi-image posts)

**Verified working against the live account, September 2026** — recorded here so nobody
has to re-probe Meta to find this out.

The flow is three steps: a container per image with `is_carousel_item=true`, then a
container with `media_type=CAROUSEL` and `children=[ids]`, then publish that. Up to 10
images, counting as **one** post against the 100/day limit.

What the probe established that the docs don't say:

- **Works on Instagram Login.** Not Facebook-Login-only, unlike the collaborators *read*
  edge (see `docs/instagram.md`).
- **`alt_text` is accepted on carousel children**, so AI alt text keeps working per image.
- **`collaborators` is accepted on the carousel container** (HTTP 200). Postiz's source
  states collaborators aren't allowed on carousels; either that's stale or Meta changed
  it. Unconfirmed whether invites actually deliver — the container being accepted isn't
  proof, and confirming means publishing one for real.
- **Every image is cropped to the FIRST image's aspect ratio.** The significant one for
  stage work: a portrait opener crops the landscapes behind it. Ordering becomes an
  editorial decision, not just sequencing.
- **Child creation fails intermittently under rapid fire** with subcode 2207052, "Only
  photo or video can be accepted as media type." It looks like bad images and isn't —
  a rejected image succeeded on the first retry when sent alone. Needs pacing and retry
  between children, or it will present as "half my photos are broken".

**The hard part is not the API.** FramePost assumes one post = one photo — the scatter
scheduler, the calendar, `post_platforms`, and analytics all rest on it. Carousels need
a grouping concept: which photos travel together, in what order, and what the other four
platforms do with a group, since none of them have an equivalent. The likely answer is
that a group posts as a carousel on Instagram and as separate posts elsewhere, but that
is a decision to make deliberately rather than discover halfway through.

Scope this before building. Rough guess once scoped: **1–2 weeks**.

---

## Instagram crop studio

**Design approved from a working prototype, September 2026.** Interactive mockup:
<https://claude.ai/code/artifact/3912b6ad-bfb0-4ccf-a070-1379fd9d84d2> — drag, zoom,
switch ratio and fit, with a live readout of the exact state to be persisted.

### Why

Flickr receives the full frame; only Instagram needs a variant. `render_variant()`
already handles all three fit modes and the face-anchored default, and `/ig-preview`
already renders precisely what the worker will apply. The gap is not the transform —
it's that `ig_crop_offset` is a single 0–1 float along the long axis.

That float is *mathematically complete* for a maximum-area crop: a portrait squeezed to
4:5 can only move vertically. What it cannot express is a **tighter** window — no way to
crop in on a performer or cut an exit sign out of the frame. Adding scale is what creates
a second degree of freedom, and only then does two-axis positioning mean anything.

On a 2:3 stage portrait at 4:5, roughly 20% of the frame is discarded before any zoom.
That is the argument for making the choice visible rather than automatic.

### Decided

- **Direct manipulation.** Drag to position, scroll/pinch or slider to zoom, rule-of-thirds
  guides, the detected face marked as a hint, and a reset-to-auto control. Fit stays a
  three-way toggle (`crop` / `pad` / `pad_blur`).
- **Schema.** `ig_crop_offset` (float) becomes `ig_crop_rect` — a normalized
  `{x, y, w, h}` in 0–1 source coordinates — stored **alongside the ratio it was authored
  against**. The target ratio is learned at runtime by the 3:4 probe, so a bare rect would
  silently change meaning if Meta's floor moved. Existing offsets migrate by deriving the
  equivalent maximum-area rect.
- **The server stays the source of truth.** The browser sends a rect; `render_variant`
  crops exactly that rect. The canvas only ever previews. `/ig-preview` remains the
  verification path, rendered on release rather than every drag frame — which also avoids
  a round-trip per mouse move. Tests must pin client/server agreement, because a preview
  that lies is worse than no preview.
- **Available on every photo**, not only out-of-ratio ones. Cropping tighter is useful on
  a square Instagram would accept untouched.

### Known gaps in the prototype

Pad and blur modes are visual approximations rather than the real renderer, and the face
marker is drawn from the stored offset rather than live detection. Both come from the
server in the real implementation.

**Effort: 3–5 days** — canvas interaction, the migration, rect support in
`render_variant`, and the parity tests.

---

## Reliability

Adopted from Postiz and Mixpost — see the adoption report for the full comparison.

- **Honour the platform's own retry-after.** *1 day.* Cache a `blocked_until` per
  credential from `Retry-After` and rate-limit reset headers instead of guessing with
  exponential backoff. When an API says exactly when to come back, blind backoff retries
  too early and then waits too long.
- **Never let cosmetic enrichment endanger a live post.** *½–1 day.* Publishing and the
  permalink lookup that follows sit in the same try block. On success, commit the remote
  id immediately and enrich the URL in a separate job.
- **Republish / idempotency guard.** *½–1 day.* Require an explicit `republish=true` for
  any target that already has an `external_id`.
- **Enforce the single-writer constraint in code.** *½ day.* Today nothing double-posts
  because there is one worker and APScheduler defaults to `max_instances=1`. There is no
  database-level claim, so a second worker replica would fire every due post twice. The
  guarantee currently lives in someone's head.
- **Publish-adapter contract tests.** *2–4 days.* Mock each platform: success, expired
  token, rate limit, rejected media, partial collaborator failure, retry exhaustion.
- **Ruff + formatter.** *½ day.* Cheap consistency against AI-assisted drift. Not mypy
  on 16k existing lines yet.

---

## Interface

- **Status density on the calendar.** *1–2 days.* Platform dots per day coloured by
  outcome, and a "needs attention" filter. The calendar shows *when*; it should also show
  what needs you. The per-platform data already exists.
- **Per-platform caption overrides with preview.** *3–5 days.* Captions are built per
  platform by rule, which is the right default, but there's no way to override one
  platform for one post and no way to see what Bluesky gets before it goes. Do it when a
  caption embarrasses you, not before.
- **Operational surface on the health page.** *1–2 days.* Last scheduler tick, next due
  post, last publish attempt, token expiry horizon, last verified backup. The route
  shadowing bug was found by reading nginx logs; the system should have said so itself.
- **Channel pill vs auth state.** *Cosmetic.* The nav shows "Flickr connected" green even
  while the banner says it needs reconnecting. Both are technically true — connected, but
  under-scoped — and the mismatch reads oddly.

---

## Blocked or waiting on someone else

- **Collaborator acceptance tracking.** Blocked, not deferred. Meta exposes collaborator
  state only on the Facebook-Login API surface; this install uses Instagram Login, where
  both `?fields=collaborators` and `/{media}/collaborators` are schema errors. Unblocking
  means a linked Facebook Page, a new token flow, and app review. Until then acceptance
  is inferred from engagement lift. See `docs/instagram.md`.
- **Instagram comment text.** The Meta app is in development mode, which filters comment
  bodies; counts are real. Live mode would unlock it.
- **Pinterest Standard tier.** Awaiting Pinterest's review. Trial mode works for the
  owner account meanwhile.

---

## Deliberately deferred

Considered and consciously put down, with the reason — so they don't get re-litigated.

- **Instagram Stories auto-post.** Wants more thought about whether Stories suit an
  archival posting rhythm at all.
- **Per-platform tag sets.** Different hashtag strategies per network.
- **Reels follow-ups.** Director mode, a history page, crossfades, a cleanup policy for
  generated MP4s, and performer aggregation across a multi-photo Reel.
- **Security hardening bundle.** Reasonable while the app stays inside the VPN perimeter
  with a single user. Revisit if it is ever exposed.
- **Mac / SwiftUI rewrite.** A separate project with its own plan, not an increment of
  this one.
- **Postgres, multi-user auth, teams, approvals.** Not deferred so much as rejected: they
  address none of this install's actual failure modes.

---

## Worth doing once, for ideas

- **Stand up Postiz in a VM.** *1 day.* Not to migrate — it has no Flickr support, which
  rules it out — but to see what a 35k-star project does better and take it. Its typed
  error taxonomy has already been adopted this way.
