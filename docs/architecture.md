# FramePost — Architecture

How a photo travels from a Lightroom export to five platforms, and which
process owns each step. Companion to the top-level README; the Instagram
specifics live in [instagram.md](instagram.md).

## Processes

Three containers share one SQLite database (WAL mode) and one photo volume:

| Container | Role |
|---|---|
| `nginx` | Serves the built React app, proxies `/api` to the backend |
| `backend` | FastAPI — auth, CRUD, uploads, previews, on-demand renders |
| `worker` | APScheduler — everything time-driven (below) |

The worker's job table (all registered in `services/scheduler.py`):

| Job | Cadence | What it does |
|---|---|---|
| `heartbeat` | 1 min | Stamps `app_config.worker_last_heartbeat` — `/health` degrades if stale |
| `fire_due_posts` | 1 min | Scans for `scheduled_at <= now`, posts to Flickr, then fans out |
| `retry_platform_posts` | 1 min | Re-attempts `post_platforms` rows whose backoff timer elapsed (also powers the UI "Post now" button) |
| `submit_due_groups` | 1 min | Flickr group-pool submissions with per-group tracking |
| `fill_missing_alt_text` | 15 min | AI alt text for any post lacking it (new imports + back catalog) |
| `daily_flickr_sync` | daily | Pulls the account's Flickr photos into the machine-tag cache (duplicate layer 2) |
| `comments/engagement sync` | daily | Comments + likes from Flickr/Bluesky/Pixelfed; counts + comments from Instagram |
| `daily_instagram_token_refresh` | daily | Refreshes the IG token inside its 7-day leeway window |
| `daily_cleanup` | daily | SQLite hot backup + rotation, original purge past retention, reel purge, IG staging-variant sweep, disk↔DB orphan scan, WAL checkpoint |

## Post lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant LR as Lightroom / upload
    participant W as watcher (worker)
    participant DB as SQLite
    participant U as User (React UI)
    participant F as fire_due_posts (worker)
    participant FL as Flickr
    participant P as Bluesky / Pixelfed / Pinterest / Instagram

    LR->>W: file lands in /mnt/photo-data/incoming
    W->>W: stability check (size+mtime settle)
    W->>DB: Post row — IPTC pre-fill, SHA-256, thumbnail, preview,<br/>privacy = Settings default
    Note over DB: alt-text sweep fills alt_text within ~15 min
    U->>DB: tag/describe (per-post or show-chip → Bulk Edit)
    U->>DB: Smart Fill → stratified scatter sets scheduled_at
    F->>DB: minute tick: scheduled_at <= now?
    F->>FL: derivative upload (EXIF preserved), albums, machine-tag
    F->>DB: status=posted, flickr_photo_id
    F->>P: fan-out to every default-target platform
    P-->>DB: post_platforms row per platform (posted / pending+retry / failed)
    Note over F,P: per-platform failures isolate — a Bluesky 500<br/>never rolls back the Flickr post
```

State machine for `Post.status`: `pending` → (`scheduled_at` set — still
`pending`) → `posted` | `late` (fired >5 min behind) | `missed` (>24 h,
needs manual re-queue) | `failed` (retries exhausted). Non-Flickr outcomes
live per-platform in `post_platforms.status` with their own retry backoff.

## Scheduling: stratified scatter + learned hours

```mermaid
flowchart TD
    A[Select N drafts] --> B["Free days = next 365 days<br/>minus already-booked days"]
    B --> C["Sort chronologically,<br/>split into N equal segments"]
    C --> D["Pick 1 random day per segment<br/>(even spread, no same-week clumps)"]
    D --> E["Shuffle assignment order<br/>(no show-chronology leak)"]
    E --> F{"Hour pool"}
    F -->|"≥5 posts/hour-bucket of<br/>engagement history"| G["Top 6 hours by likes + 2×comments,<br/>clamped 08:00–23:00 local"]
    F -->|not enough data| H["Fallback: 9-11 AM / 6-8 PM"]
    G --> I["Random hour + 0-5 min fuzz<br/>+ random seconds"]
    H --> I
    I --> J[Dry-run preview → confirm]
```

Later batches stratify over the *remaining* free days, so successive shows
interleave instead of stacking. The learned pool recomputes from
`engagement_snapshots` on every run and is surfaced by
`GET /api/schedule/popular-hours`.

## Instagram pipeline (summary)

```mermaid
flowchart LR
    A[Post fired to Flickr] --> B{aspect within<br/>accepted range?}
    B -->|yes| C["image_url = Flickr Large-1600<br/>derivative URL"]
    B -->|no| D["Render variant: face-anchored crop<br/>(or pad / blurred pad), per-post<br/>nudge offset honored"]
    D --> E["Stage as hidden Flickr photo<br/>machine-tag framepost:ig_variant"]
    E --> F["image_url = Large-2048 derivative<br/>(never the _o Original — Meta rejects it)"]
    C --> G["POST /media container"]
    F --> G
    G --> H{status_code}
    H -->|FINISHED| I["POST /media_publish → permalink"]
    H -->|aspect error on 3:4 probe| J["Record 4:5 floor, regen variant, retry once"]
    J --> G
    I --> K["Delete staging photo<br/>(daily sweep catches strays)"]
```

Full detail, constraints, and the empirical findings (3:4 acceptance,
`_o` URL rejection, dev-mode comment filtering) in
[instagram.md](instagram.md).

## Engagement + activity

Daily sync (`services/comments.py`) writes three tables the UI reads:

- `post_comments` — real comment rows, deduped by `(platform, remote_id)`,
  `seen_at` powers the unread badge
- `post_likes` — per-user likes where the platform exposes them (Flickr,
  Bluesky, Pixelfed; Instagram never does)
- `engagement_snapshots` — append-only count series per (post, platform);
  powers analytics, the learned scheduler hours, and the Activity stream's
  synthesized Instagram `▲ +N likes · +M comments` delta items

## Safety nets

- **Duplicates**: SHA-256 at import (layer 1) + Flickr machine-tag cache
  (layer 2) + soft title/date/dims warning
- **Backups**: nightly SQLite hot backup with 7/4/3 rotation to
  `/mnt/photo-data/backup` (off-host replication is the known open item —
  see [restore.md](restore.md))
- **Orphan scan** (daily): files without DB rows quarantine to
  `errors/orphans/`; posted photos missing their permanent thumbnail are
  flagged loudly
- **Token health**: `/health` carries `platform_warnings`; the UI banner
  fires when a token is expired or auto-refresh has been failing for days
- **Tests**: `backend/tests/` — scheduling math, caption building, hashtag
  legality, IG crop geometry. Run inside the container:
  `docker compose exec backend python -m pytest tests/ -q`
