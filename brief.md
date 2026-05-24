# FramePost — Project Brief
> Complete handoff document for Claude Code. Read this file in full before writing any code.

---

## What this is

A self-hosted photo scheduling web application for a professional photographer. It replaces manual social media posting with an automated pipeline: import high-res images (watched folder or browser upload), review them in a draft queue, schedule them on the calendar, and the app posts them automatically at the right time to Flickr.

The app is named **FramePost**.

The daily workflow is **queue-first**: a Lightroom export lands in the watched folder, FramePost imports it as a draft and pre-populates title/description/tags from any IPTC metadata Lightroom wrote into the file, you review and refine in the Draft Queue, click Schedule, and walk away. The Draft Queue — not a calendar — is the primary working surface.

**Scope: Flickr only.** No Instagram or other platforms. The schema is Flickr-shaped throughout. If a future version ever adds another platform, it would be a focused effort with its own schema migration.

---

## Infrastructure

| Decision | Choice |
|---|---|
| Server | Ubuntu Server LTS VM, static private IP |
| Specs | **4 vCPU, 8 GB RAM, 80 GB OS disk** |
| Photo storage | **Separate mounted volume, 500 GB – 1 TB** at `/mnt/photo-data` |
| Access | Private network only, VPN for remote access |
| Deployment | Docker Compose — single `docker-compose up` to run |
| Database | SQLite (WAL mode) |
| Users | Single user only |
| Auth | Username/password login, Argon2id password hashing, CSRF protection, session timeout configurable, first-run admin via CLI |

The app is never exposed to the public internet. No SSL certificate required. Accessible via browser on local network or VPN.

### Why two disks?
The OS disk holds the OS, Docker, the app code, and the SQLite database. The photo storage volume holds originals, thumbnails, error files, and DB backups. Separating them means: the photo volume can be resized independently as the archive grows, the volume can be snapshotted by the hypervisor without touching the OS, and an OS reinstall doesn't risk the archive. The SQLite database stays on the OS disk for performance (WAL mode prefers a journaled local FS).

### Storage math
60–70 MP JPEGs at high export quality typically run 25–80 MB each. With 200 posts/month at ~50 MB average and 30-day retention, the originals working set is roughly 10–15 GB at any one time. Thumbnails (~200 KB each, kept permanently) accumulate at ~2.5 GB per 12,000 photos. A 500 GB volume is generous; 1 TB gives long-term breathing room and headroom for occasional large batch imports.

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python + FastAPI | API, image processing, scheduling |
| Frontend | React | Single page app |
| Database | SQLite (WAL mode) | Lightweight, no setup, perfect for this scale |
| Scheduler | APScheduler (Python) | Real-time post firing + cron-style nightly jobs |
| Image processing | Pillow + piexif | Resize, thumbnail, EXIF extract |
| IPTC extraction | IPTCInfo3 (pure Python) | Read Lightroom-written title, caption, keywords; ExifTool subprocess as fallback if encoding edge cases appear |
| Folder watcher | watchdog | For watch-folder import path |
| Containerisation | Docker Compose | Two app services: web + worker |
| AI tagging | Claude Vision API | Suggests tags on image upload |
| Token encryption | `cryptography` (Fernet) | Encrypts OAuth tokens at rest |
| Password hashing | `passlib[argon2]` or `pwdlib` | Argon2id for the admin password |

---

## Two Background Processes

(The nightly cleanup is an APScheduler job inside the worker, not a separate container.)

1. **Web application** — FastAPI backend + React frontend. Handles all UI, API calls, image uploads, watch-folder events, the `/health` endpoint.
2. **Worker** — One APScheduler instance running:
   - Real-time post-firing job (checks every minute for due posts)
   - Group submission job (separate queue, runs after main post succeeds)
   - Daily Flickr sync job (refreshes `flickr_photos` cache, default 4am)
   - Daily cleanup job (purges expired originals + DB backup, default 3am)
   - Heartbeat — writes a timestamp to `app_config.worker_last_heartbeat` every minute so the `/health` endpoint can detect a dead worker

---

## The Daily Flow

The architecture is shaped by how the photographer actually works:

1. Edit and finish photos in Lightroom. Add titles, captions, and keywords there — Lightroom writes them into the JPEG's IPTC metadata on export.
2. Export to the watched folder (`/mnt/photo-data/incoming/` mounted via SMB on the Lightroom workstation).
3. FramePost detects the new files, runs the import pipeline (stability check → EXIF → IPTC → hash → duplicate check → thumbnail → queue). Drafts appear in the Draft Queue with title/description/tags pre-populated from the IPTC data.
4. Open FramePost in the browser. Click a draft and the right-side panel becomes a metadata editor showing the pre-populated fields plus albums, groups, privacy, scheduled date/time. Refine as needed.
5. Click **Schedule**. The post moves out of Drafts and into Scheduled.
6. The worker fires the post at the scheduled time. The post moves into Published.

The Draft Queue is the primary working surface. The calendar (Scheduled view) is secondary — used for seeing distribution, batch operations, and rescheduling. Browser upload is a fallback for ad-hoc additions when away from the Lightroom workstation.

---

## Flickr Organization Model

Before any UI or API code, the app should be informed by a clear mental model of how Flickr is organized. These four concepts mean different things and the app should treat them differently:

- **Albums (photosets)** — *How humans browse your portfolio.* Curated buckets like "Best of Burlesque," "Stage Performance Favorites," "New Orleans Nightlife." Albums should be small in number, intentional in curation, built around how a viewer would want to explore your work — not auto-generated per shoot or per upload date.

- **Tags** — *Search and discovery metadata.* Performer name, venue, city, year, genre, lighting style, camera/lens, event type. Tags make a single photo findable from many angles.

- **Collections** — *Broad public sections* on the photographer's profile. Examples: Live Performance, Burlesque & Cabaret, Concert Photography, Portraits & Promo, New Orleans Stage. Collections group multiple albums.

- **Groups** — *Selective community distribution,* not a filing system. Submitting to a group asks that group's pool to display the image to its audience. Groups have rules, limits, and moderation. The app must make group submission easy but should not encourage spammy distribution.

This model directly shapes the metadata editor: title/description/tags/albums are the core fields for *every* post; groups are an optional, deliberate, per-post choice with guardrails.

---

## Image Pipeline & Storage

### Two import paths
1. **Watch folder** (primary, Phase 2) — Lightroom or any export tool drops files into `/mnt/photo-data/incoming/`. The watcher detects them, runs the stability check + hashing + EXIF + IPTC + thumbnail pipeline, queues them as drafts. Intended daily intake.
2. **Browser upload** (fallback, Phase 1) — drag-and-drop in the Draft Queue or via a file picker. Same pipeline, different trigger. Used for ad-hoc additions, mobile/remote uploads.

Both paths share the same import pipeline; only the trigger differs. Browser upload is built first because it doesn't require SMB/share configuration and is testable from a single button.

### Storage layout (on the mounted volume)
```
/mnt/photo-data/
├── incoming/        # watch-folder drop zone (Phase 2)
├── originals/       # imported originals (30-day retention)
├── thumbnails/      # permanent archive thumbnails
├── derivatives/     # short-lived: Flickr-resized files awaiting upload
├── errors/          # files that failed import, with sidecar .log files
└── backup/          # rotated SQLite backups
```

(The SQLite database itself lives on the OS disk at `/app/data/framepost.db` for performance.)

### Large-file handling (60–70 MP images)
A single 70 MP JPEG can be 60–80 MB. The pipeline must handle this without loading multiple files into memory at once:

- **Nginx**: `client_max_body_size 200M;` — generous ceiling for large exports.
- **FastAPI upload endpoint**: stream uploads to disk via `UploadFile` (don't read all bytes into memory). Compute SHA256 and write to `originals/` in the same streaming pass.
- **Pillow / thumbnail generation**: open with `Image.open()`, immediately call `thumbnail()` with a max dimension (1600px long edge for filmstrip, 320px for grid). Never call `.load()` on the full image. Set `Image.MAX_IMAGE_PIXELS = 200_000_000` in trusted import code. This supports 60–70 MP exports while keeping a decompression-bomb ceiling instead of disabling Pillow's protection entirely.
- **EXIF/IPTC extraction**: read from the bytes-on-disk so the full image isn't held in memory.
- **Worker**: process one image at a time. With 8 GB RAM, single-image processing is comfortable; concurrent processing of multiple 70 MP files is not, and is unnecessary at this scale.
- **Derivatives at post time**: when the worker fires a post, it generates the Flickr-sized JPEG into `derivatives/`, uploads, then deletes the derivative. This bounds derivative storage to the size of the in-flight post.

### File-stability and import pipeline
Every incoming file goes through the same pipeline, regardless of import path:

1. File arrives (uploaded to `originals/` or detected in `incoming/`).
2. **Disk-full check**: if free space on the photo volume is below the hard-stop threshold (default 5 GB), refuse with HTTP 507 Insufficient Storage. No partial writes.
3. For watched folder: wait 10–30 seconds, re-check file size and mtime, repeat until stable. (Browser uploads skip this — the upload completion *is* stability.)
4. Open and validate as a real JPEG/PNG with Pillow.
5. Extract EXIF metadata (capture date, camera, lens, ISO, shutter, aperture, focal length, GPS if present) via piexif.
6. Extract IPTC metadata (title, description/caption, keywords) via IPTCInfo3.
7. Compute SHA256 hash of file bytes.
8. Run duplicate check (see Duplicate Prevention).
9. Generate thumbnail (320px long edge, JPEG quality 85) into `thumbnails/`.
10. Insert into queue with status `pending`, IPTC fields pre-populated into `title`/`description`/`tags`, and `imported` event in `post_events`.
11. If any step fails, move the file to `errors/` with a `.log` sidecar describing the reason; surface in the UI.

### Pre-populated metadata from Lightroom
When IPTC data is present in an imported file, FramePost copies it into the draft post:
- IPTC `Object Name` → `posts.title`
- IPTC `Caption-Abstract` → `posts.description`
- IPTC `Keywords` → `posts.tags`

The user can review and refine in the metadata editor before scheduling. If no IPTC data is present, the fields remain blank and the user fills them in manually. IPTC is read once on import; subsequent edits to the file in Lightroom do not re-trigger updates.

### What gets stored
- **Originals** — 30-day retention (configurable). Auto-purged by the daily cleanup job *only after* the post is in `posted` status and the thumbnail file exists.
- **Thumbnails** — permanent. The photographer's archival record of "what was posted when."
- **Derivatives** — generated on the fly, deleted immediately after successful upload.

### Storage warning and hard stop
Disk usage of the photo volume is shown live in Settings → System.
- At the **warning threshold** (default 80%), an amber badge appears in the top nav.
- At the **hard-stop threshold** (default 5 GB free remaining), new imports are refused at the upload endpoint and the watch folder pipeline. The badge becomes red. This prevents partial-write corruption when the disk fills mid-import.

---

## Scheduling Logic

### One post per hour
Maximum one post per hour. Keeps the feed paced and prevents accidental flooding. The Scheduled calendar greys out time slots already taken; the metadata editor's time picker rejects them.

### Missed post recovery
If the worker is down or the network fails when a post is due:

- **Within 24 hours of scheduled time** — auto-post on next worker tick, log as `late`.
- **More than 24 hours past** — flag `missed`, surface in the top-nav red badge counter. User triages: repost now, reschedule, or dismiss.

### Status lifecycle
`pending` → `posted` (green) | `late` (amber) | `missed` (red) | `failed` (red)

Used consistently across Draft Queue, Scheduled, and Published views. Every status transition writes a row to `post_events` (see Data Model).

---

## System Health & Failure Handling

### Retry policy
Failed Flickr operations (uploads, group submissions, sync calls) retry with bounded exponential backoff:

- **5 maximum attempts**
- **Backoff intervals**: 1m, 5m, 15m, 1h, 4h
- After the 5th failure, the post moves to `failed` status and surfaces in the dashboard's red badge for manual triage.
- Permanent validation errors (HTTP 400, image rejected by Flickr, etc.) skip retry and go straight to `failed` immediately.
- Retry parameters are configurable via `app_config.retry_max_attempts` and `app_config.retry_backoff_minutes`.

Each retry attempt writes a `flickr_failed` row to `post_events` with the attempt number and sanitized error details.

### Disk-full hard stop
The import pipeline refuses new files when free space on the photo volume drops below the hard-stop threshold (default 5 GB, configurable via `app_config.storage_hardstop_gb`). The endpoint returns HTTP 507 with a clear error message. The watch folder pipeline moves rejected files to `errors/` with a `.log` explaining the disk-full state.

### Health endpoint
`GET /health` returns a JSON payload with:
- `status`: `ok` | `degraded` | `down`
- `worker_alive`: boolean (last heartbeat within 2 minutes)
- `db_writable`: boolean
- `photo_volume_writable`: boolean
- `photo_volume_free_gb`: number
- `flickr_last_success`: ISO timestamp or null
- `last_backup`: ISO timestamp or null
- `version`: app version string

Responds HTTP 200 if status is `ok` or `degraded`, HTTP 503 if `down`. Suitable for UptimeRobot-style monitoring from inside the VPN. No authentication required (LAN-only, VPN-protected).

### In-app status banner
When `/health` reports `degraded` or `down`, a banner appears at the top of every page describing what's wrong (worker offline, disk full, Flickr unreachable, last backup overdue, etc.) with actionable links. The banner is dismissible per-session but reappears on the next page load if the underlying condition persists.

---

## Tag Profiles

Reusable bundles of tags applied in one click. Profiles can be stacked on a single post — tags are merged and deduplicated.

### Profile structure
- Name
- Tags (plain text, comma-separated)
- "Always apply" toggle (global default flag)

### Global default profile
Always applied to every post. Cannot be deleted, only edited. Used for evergreen tags (photographer name, city, genre).

### Tag limits
Flickr has no hard tag limit. Warn above 75 tags per post.

---

## AI Tagging

On image upload, optionally send a resized preview image to Claude Vision API and request tag suggestions. User reviews before scheduling — accept, edit, or reject each suggestion. AI tagging is opt-in and must clearly disclose that enabled images/previews leave the local server for analysis. Full-resolution originals are never sent unless a future setting explicitly allows it.

If IPTC keywords are already present from Lightroom, AI suggestions appear as *additional* candidates, not replacements — the user sees the existing tags plus suggested additions.

Settings:
- Enable/disable toggle (default off)
- Max suggestions per image (default 10)
- Auto-apply high-confidence tags toggle (default off)
- Also draft a description suggestion (default on, but only if IPTC caption is empty)
- Send full-resolution toggle (default off — sends a downscaled preview)

---

## Batch Scheduling — "Smart Fill"

User selects multiple drafts from the queue, sets a target cadence (e.g. one per day at 10am). The app distributes them across the calendar respecting the one-post-per-hour rule and existing scheduled posts. User reviews the proposed distribution before confirming.

---

## Group Posting Strategy

Groups are handled with deliberate friction. Easy enough to use; not so frictionless that you accidentally spam the same image across 30 groups.

### Per-post rules
- **Default max groups per photo:** 5
- **Warn above 8 groups** with a confirmation dialog
- **Manual confirmation always required** — explicit "Submit to groups" click, not bundled into the main post action

### Per-group metadata
- Group name + Flickr group ID
- Category (Burlesque/Stage, Live Music, Portrait, etc.)
- Daily submission limit (per Flickr group rules)
- Content restrictions / safety level expectations
- No-watermark rules
- Theme notes
- Default-enabled flag (whether pre-selected for posts in its category)

### Submission tracking
Each group submission tracked separately. A single post's image upload to Flickr may succeed but its submission to Group X may fail and Group Y may be rejected. Per-submission states: `pending`, `submitted`, `accepted`, `failed`, `rejected`. Submissions retry on the same policy as posts (5 attempts with backoff).

### Example group presets
```
Burlesque / Stage:  Burlesque Photography, Cabaret, Stage Performers, New Orleans Nightlife
Live Music:         Concert Photography, Live Music Photography, Stage Lighting, New Orleans Music
Portrait:           Portrait Photography, Performer Portraits, Environmental Portraits, B&W Portraits
```

---

## Duplicate Prevention

Two-layer detection: local hash matching, plus Flickr-side machine-tag stamping.

### Layer 1 — Local SHA256 hash
Every imported file is hashed. Hash stored on the post row. New uploads check for an existing matching hash; if found, user gets a "this looks identical to a photo you've already imported" warning with a link to the existing record. User can override.

### Layer 2 — Flickr machine tag stamping
On upload, the worker adds a hidden machine tag:
```
framepost:sha256=<hash>
```
Searchable via Flickr API but hidden from public tag display. The app can query Flickr for `machine_tags=framepost:sha256=<hash>` and reliably detect "did *this app* already post this exact file?"

A daily Flickr sync caches all the photographer's Flickr photos and machine tags into the local `flickr_photos` table for fast offline duplicate checks.

### Pre-publish check
Just before posting:
1. Hash already in `flickr_photos` cache with a `framepost:sha256=` tag? → block + warn.
2. Any Flickr photo with matching title + date taken + dimensions? → soft warn (covers older photos uploaded outside the app).
3. Otherwise → proceed.

### Future enhancement (v2+)
Perceptual hashing (pHash) for visually-similar files with different export settings. Out of scope for v1.

---

## Data Model (SQLite)

### Database mode
SQLite runs in **WAL mode** with `synchronous=NORMAL`. Set on first connection:
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;  -- 256 MB
```
WAL allows the worker to write while the web app reads, with no contention. `synchronous=NORMAL` is safe with WAL and faster than `FULL`.

### Backups
Hot backup using SQLite's online backup API (don't `cp` the file — it's not crash-safe under WAL). Daily backup at 3am via the cleanup job:
```python
con = sqlite3.connect('framepost.db')
bck = sqlite3.connect(f'/mnt/photo-data/backup/framepost-{date}.sqlite')
con.backup(bck)
bck.close(); con.close()
```
Retention: 7 daily, 4 weekly, 3 monthly. Weekly = Sunday's daily promoted; monthly = first Sunday's weekly promoted. Older backups deleted. Backups land on the photo volume, so they survive an OS-disk failure.

### Tables

**posts**
```
id                 TEXT PRIMARY KEY
title              TEXT
description        TEXT
tags               TEXT
scheduled_at       DATETIME
posted_at          DATETIME
status             TEXT (pending | posted | late | missed | failed)
original_filename  TEXT
original_path      TEXT
thumbnail_path     TEXT
file_size_bytes    INTEGER
width              INTEGER
height             INTEGER
sha256             TEXT (indexed)
captured_at        DATETIME (from EXIF DateTimeOriginal)
camera_make        TEXT
camera_model       TEXT
lens               TEXT
focal_length       REAL
iso                INTEGER
shutter_speed      TEXT (e.g. "1/500")
aperture           REAL
gps_lat            REAL
gps_lng            REAL
exif_raw           TEXT (JSON blob — full EXIF for archival/future use)
iptc_raw           TEXT (JSON blob — full IPTC as imported, for archival)
privacy            TEXT (private | friends_family | public)
safety_level       TEXT (safe | moderate | restricted)
content_type       TEXT (photo | screenshot | other)
flickr_photo_id    TEXT
flickr_url         TEXT
retry_count        INTEGER (default 0)
next_retry_at      DATETIME
error_message      TEXT
created_at         DATETIME
updated_at         DATETIME
```

**post_events** (audit trail — every state change for every post)
```
id          INTEGER PRIMARY KEY AUTOINCREMENT
post_id     TEXT (FK posts.id, indexed)
event_type  TEXT  (imported | scheduled | rescheduled | flickr_uploading
                 | flickr_uploaded | flickr_failed | group_submitted
                 | group_accepted | group_rejected | marked_late
                 | marked_missed | manual_repost | manual_dismiss
                 | edited | original_purged | deleted)
actor       TEXT  (user | system | worker)
details     TEXT  (JSON blob: error messages, attempt numbers, old/new
                  times, group IDs, Flickr response, etc.)
created_at  DATETIME (indexed)
```
Used for debugging, history reconstruction, and the per-post "activity timeline" in the Published detail modal.

**tag_profiles**
```
id          TEXT PRIMARY KEY
name        TEXT
tags        TEXT
is_default  INTEGER (0 or 1)
sort_order  INTEGER
created_at  DATETIME
```

**post_profiles** (many-to-many)
```
post_id     TEXT
profile_id  TEXT
PRIMARY KEY (post_id, profile_id)
```

**albums**
```
id              TEXT PRIMARY KEY
flickr_album_id TEXT
name            TEXT
description     TEXT
photo_count     INTEGER
last_synced_at  DATETIME
```

**post_albums** (many-to-many)
```
post_id   TEXT
album_id  TEXT
PRIMARY KEY (post_id, album_id)
```

**groups**
```
id               TEXT PRIMARY KEY
flickr_group_id  TEXT
name             TEXT
category         TEXT
daily_limit      INTEGER
content_notes    TEXT
no_watermark     INTEGER (0 or 1)
default_enabled  INTEGER (0 or 1)
created_at       DATETIME
```

**post_groups** (many-to-many with submission state)
```
id             TEXT PRIMARY KEY
post_id        TEXT
group_id       TEXT
status         TEXT (pending | submitted | accepted | failed | rejected)
submitted_at   DATETIME
retry_count    INTEGER (default 0)
next_retry_at  DATETIME
error_message  TEXT
```

**flickr_photos** (local cache for duplicate detection + sync)
```
flickr_photo_id  TEXT PRIMARY KEY
title            TEXT
machine_tags     TEXT
date_taken       DATETIME
date_uploaded    DATETIME
url              TEXT
width            INTEGER
height           INTEGER
album_ids        TEXT (JSON array)
last_synced_at   DATETIME
```

**platform_credentials** (encrypted at rest — see Security & Operational)
```
id              TEXT PRIMARY KEY
platform        TEXT (always "flickr" for v1)
access_token    TEXT (Fernet-encrypted)
refresh_token   TEXT (Fernet-encrypted)
token_expires   DATETIME
account_name    TEXT
key_version     INTEGER (which encryption key was used — supports rotation)
connected_at    DATETIME
```

**app_config**
```
key    TEXT PRIMARY KEY
value  TEXT
```

### Required indexes and foreign keys
Create explicit indexes for the fields used by queue views, scheduling, duplicate checks, and history filters:

```sql
CREATE INDEX IF NOT EXISTS idx_posts_sha256 ON posts(sha256);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_scheduled_at ON posts(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_posts_next_retry_at ON posts(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_posts_flickr_photo_id ON posts(flickr_photo_id);
CREATE INDEX IF NOT EXISTS idx_post_events_post_id ON post_events(post_id);
CREATE INDEX IF NOT EXISTS idx_post_events_created_at ON post_events(created_at);
CREATE INDEX IF NOT EXISTS idx_flickr_photos_title ON flickr_photos(title);
CREATE INDEX IF NOT EXISTS idx_flickr_photos_date_taken ON flickr_photos(date_taken);
CREATE INDEX IF NOT EXISTS idx_post_groups_post_id ON post_groups(post_id);
CREATE INDEX IF NOT EXISTS idx_post_groups_group_id ON post_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_post_groups_next_retry_at ON post_groups(next_retry_at);
```

Use foreign keys with cascade cleanup for child records tied to a post:

```
post_events.post_id   -> posts.id  ON DELETE CASCADE
post_profiles.post_id -> posts.id  ON DELETE CASCADE
post_albums.post_id   -> posts.id  ON DELETE CASCADE
post_groups.post_id   -> posts.id  ON DELETE CASCADE
```

### Initial app_config seed values
Seeded at first-run setup:

```
timezone                  = "America/Chicago"
studio_name               = "Darrell Miller Photography"
theme                     = "dark"
start_page                = "draft_queue"
session_timeout_minutes   = 1440      # 24 hours
photo_root                = "/mnt/photo-data"
upload_max_mb             = 200
original_retention_days   = 30
storage_warning_percent   = 80
storage_hardstop_gb       = 5
cleanup_time              = "03:00"
flickr_sync_time          = "04:00"
ai_tagging_enabled        = false
ai_max_suggestions        = 10
ai_send_full_resolution   = false
watch_folder_enabled      = false     # off until path is configured
watch_folder_path         = ""
default_privacy           = "private"
default_safety_level      = "safe"
default_content_type      = "photo"
default_publish_time      = "09:00"
max_groups_default        = 5
warn_groups_threshold     = 8
retry_max_attempts        = 5
retry_backoff_minutes     = "1,5,15,60,240"
worker_last_heartbeat     = ""        # written by worker every minute
```

---

## UI — Screen by Screen

### 1. Login
Single-page username/password form. On success, sets a session cookie. The first admin account is created via the `create-admin` CLI command (see Security & Operational); there is no public sign-up.

### Upload/import UX requirements

All import paths must expose clear, per-file feedback:
- Upload progress bar for browser uploads
- Import pipeline status: uploading, validating, hashing, extracting EXIF, extracting IPTC, thumbnailing, queued, failed
- Retry failed upload/import
- Clear failed item
- View error details from the `.log` sidecar in `errors/`
- Duplicate warning dialog with link to the existing record and an explicit "upload anyway" override

For watch folder imports, the Draft Queue header shows a live status indicator: monitoring, last detected file, count of pending imports, any error count.

### 2. Draft Queue (default landing page)

The primary working surface.

**Top nav**
- Logo: "frame**post**" (teal accent on "post")
- Connection pill: "● Flickr Connected" (green dot) or "○ Flickr Disconnected" (red)
- Links: Draft Queue (active) | Scheduled | Published | Settings
- Right side: notification bell, account avatar
- Red badge on Scheduled: count of `missed` posts; click jumps to Scheduled with missed posts highlighted
- System banner above the nav when `/health` reports degraded or down

**Stats row** (4 cards)
- Drafts (pending, no schedule)
- Ready to Schedule (drafts with metadata complete)
- Scheduled This Week
- Published (all-time)

**Main area — Draft Queue grid**
- Header: "Draft Queue" + helper text
- Filter button (status, date range, camera, tags) and Sort dropdown (Newest, Oldest, Largest, Capture date)
- Thumbnail grid (4 columns standard, responsive)
- Each card: thumbnail (cover-cropped), filename, capture date, megapixel count, dimensions, status pill (Draft / Ready / Failed)
- Selectable: clicking a card highlights it (teal border) and populates the right-side editor
- Multi-select via checkboxes for batch operations
- Pagination at the bottom

**Right side panel — Metadata Editor**
- Image preview at top with "View full size" link
- Read-only file info: dimensions, megapixels, format, size, capture date, camera, lens, ISO, shutter, aperture
- Title (text input — pre-populated from IPTC if present)
- Description (textarea — pre-populated from IPTC if present)
- Tags (chip input with autocomplete — pre-populated from IPTC keywords if present; profile selector to apply tag profiles in bulk; AI suggestions appear as suggested chips when AI tagging is enabled)
- Albums (multi-select chip)
- Groups (multi-select chip with inline counter "3 / 5 groups", warn dialog at 8)
- Privacy (Private / Friends & Family / Public — defaults to Private)
- Safety level (Safe / Moderate / Restricted — defaults to Safe)
- Content type (Photo / Screenshot / Other — defaults to Photo)
- Publish Date & Time (date picker + time picker; greys out taken slots; defaults to next available 9am slot)
- Time zone (read-only display: America/Chicago)
- Primary action: "Schedule on Flickr" (teal button)
- Secondary action: "Save as draft" (link)

**Bottom — Schedule Overview** (compact, two columns)
- Upcoming Scheduled: next 3 with thumbnail, filename, date/time, "Tomorrow" / "In N days" pill
- Recently Published: last 3 with thumbnail, filename, date/time, green checkmark

**Bottom-left sidebar widget — Watch Folder Import**
- Status indicator: Active (green) / Inactive (grey) / Error (red)
- Path display
- "Open Folder" link
- Last imported file timestamp

**Empty state** (no drafts)
- Centred icon, "Your queue is clear"
- Helper: "Drop photos into your watch folder, or upload directly from your browser."
- "Upload from browser" button (teal), "View watch folder settings" link

### 3. Scheduled (calendar view)

Read-mostly. Used for distribution overview, rescheduling, and batch operations.

**Top filters bar**
- View switch: Month / Week / Agenda
- Filter chips: All / Pending / Late / Missed
- "Smart fill" button (opens cadence dialog for batch scheduling drafts)

**Calendar grid**
- 7 columns, month nav
- Each cell: day number + up to 3 scheduled-post chips (coloured dot + time + thumbnail-tip on hover)
- Click a chip → small modal with full metadata, "Reschedule," "Edit," "Cancel schedule (return to drafts)"

**Left sidebar — Drag-to-reschedule queue**
- Scrollable list of any drafts marked Ready
- Drag a draft onto a calendar slot to schedule directly from this view

### 4. Published (history)

**Top filters bar**
- Search (titles, tags, EXIF camera/lens — real-time)
- Status filter chips (Posted / Late / Missed / Failed) — toggleable
- View switch: grid / list

**Grid view** — thumbnail tiles in monthly groups, status pill, click opens detail modal.

**List view** — same data as rows: thumbnail, title, tags, date, status pill.

**Detail modal** — full metadata, EXIF readout, IPTC readout, Flickr URL ("View on Flickr"), group submission results, **activity timeline from `post_events`** (every state change, timestamped, with details).

**Stats row at top** — total posted, this month, success rate, missed count.

### 5. Settings (eight tabs)

- **General** — workspace name, time zone, theme (locked dark for v1), start page, auto-save edits.
- **Flickr** — OAuth connection status, account name, token expiry, reconnect/disconnect/test upload, default privacy/safety/content type.
- **Import** — managed library path display, supported file types, duplicate handling (warn/block/allow), originals retention slider, watch folder enable toggle and path.
- **Tag Profiles** — list with edit/set-default/delete, "+ Add" at bottom.
- **Albums** — synced Flickr albums, last sync, "Sync now," default album selector.
- **Groups** — list grouped by category, rule notes, daily limits, default-enabled toggles, "+ Add" via Flickr group picker.
- **AI Tagging** — enable, privacy disclosure, send full-resolution toggle, max suggestions, auto-apply, suggest description.
- **System** — disk usage bar with hard-stop indicator, retention slider, storage warning threshold, cleanup time, Flickr sync time, account section (username, change password, session timeout), DB backup status (last backup, retained backups, "Run backup now"), system health status (worker heartbeat, last Flickr success, photo volume status, app version).

---

## Phased Build Plan

### Phase 1 — Foundation + Draft Queue UI (weeks 1–2)
- Docker Compose: backend + worker + frontend + nginx
- FastAPI structure, React scaffold with routing
- SQLite schema with WAL pragmas + migrations + seed `app_config` values
- CLI admin commands: `create-admin`, `reset-password`, `generate-encryption-key`, `backup`
- Argon2id password hashing, login + session management + CSRF protection
- Browser image upload endpoint with streaming, large-file support (200 MB ceiling)
- Disk-full hard stop check at the upload endpoint
- File-stability validation, EXIF + IPTC extraction, SHA256 hashing, dimensions + filename capture
- Local duplicate detection (warn, allow override)
- Thumbnail generation
- `post_events` audit table with `imported` event written for every new file
- Storage layout under `/mnt/photo-data/`
- Token encryption helpers (Fernet)
- `/health` endpoint with worker heartbeat detection
- Draft Queue page: thumbnail grid, click-to-select, right-side metadata editor (basic fields). IPTC values from import populate title/description/tags automatically.
- Stats row at top of Draft Queue (drafts count and placeholders for the others)

**Deliverable:** Set up the admin account via CLI, log in, upload images via browser (including 70 MP), see them in the Draft Queue with title/description/tags pre-populated from any IPTC data. `/health` reports green.

### Phase 2 — Scheduling core + watch folder (weeks 3–4)
- Schedule action in metadata editor (date + time picker, one-post-per-hour rule enforced)
- Scheduled view: calendar grid, drag-to-reschedule sidebar, click-chip-to-edit modal
- APScheduler worker (stub — doesn't post yet, but logs `scheduled` events and fires missed-post detection)
- Missed post recovery logic (24-hour window)
- Watch folder import: `watchdog` observer on `/mnt/photo-data/incoming/`, file-stability gate, same import pipeline as browser uploads
- Watch folder status indicator on the Draft Queue sidebar
- Settings → Import: watch folder enable toggle, path display

**Deliverable:** Drop a Lightroom export into the incoming folder → it appears in Draft Queue with metadata pre-populated. Edit metadata, click Schedule, it appears on the Scheduled calendar. Missed-post logic fires correctly. All transitions logged in `post_events`.

### Phase 3 — Flickr integration (weeks 5–6)
- Flickr OAuth flow in Settings → Flickr
- Encrypted token storage with key version tracking
- Image resize pipeline: Flickr-optimised JPEG into `derivatives/` at post time, deleted after upload
- Worker fires real Flickr posts at scheduled times
- Machine tag stamping (`framepost:sha256=<hash>`)
- Retry policy implementation (5 attempts, exponential backoff, transitions to `failed`)
- Status transitions written to `post_events`
- Published view (grid + list, status pills, filters, detail modal with activity timeline)
- Stats row real numbers wired up

**Deliverable:** Working app for single-photo posting. Watch folder → Draft Queue → schedule → post to Flickr (with machine tag) → appears in Published with full activity timeline. Failures retry on schedule.

### Phase 4 — Albums, groups, Flickr-side duplicate prevention (weeks 7–8)
- Flickr album sync job
- Add-to-album multi-select in metadata editor
- Groups CRUD in Settings → Groups
- Group multi-select in metadata editor with max-5/warn-8 guardrails
- Per-group submission tracking (`post_groups`)
- Group submission worker with same retry policy as posts
- Pre-publish duplicate check against `flickr_photos` cache
- Soft-match warning for older Flickr photos (title + date + dims)

**Deliverable:** Posts can join multiple albums and submit to multiple groups, each tracked separately. Re-uploads of previously-published files are caught.

### Phase 5 — Tagging intelligence (weeks 9–10)
- Claude Vision API integration on upload (resized preview only, with disclosure)
- Tag suggestion UI in metadata editor with review/edit (additive to IPTC keywords)
- Tag profiles CRUD in Settings → Tag Profiles, profile cards, global default
- Profile stacking (merge + dedupe)

**Deliverable:** AI suggests tags on import (alongside IPTC), profiles apply on schedule, all editable.

### Phase 6 — Polish, batch tools, backups, status UI (weeks 11–12)
- Smart fill batch scheduling (multi-select drafts → cadence dialog → distribute)
- Configuration panel: all sliders/toggles wired to `app_config`
- Storage section: live disk usage, warning + hard-stop badges in top nav
- Daily cleanup job in worker: purges expired originals (only when post=`posted` and thumbnail exists), runs SQLite hot backup, rotates backups (7 daily / 4 weekly / 3 monthly)
- DB backup status surfaced in Settings → System with "Run backup now" button
- System health status panel in Settings → System (worker heartbeat, last Flickr success, photo volume free space, last backup)
- In-app status banner wired to `/health`

**Deliverable:** Production-ready for daily use. Failure modes are visible, recoverable, and documented.

---

## Additional Build Guardrails

- **Do not read large image uploads fully into memory.** Stream to disk, hash while streaming, then process one image at a time.
- **Make all worker actions resumable.** Worker restarts must not duplicate uploads, duplicate group submissions, or lose error state. Re-check `posts.status`, `flickr_photo_id`, and the duplicate cache before any external action.
- **Prefer explicit state transitions.** All transitions go through service functions that update `posts.status` and write `post_events` together.
- **Keep UI and worker logic separate.** React calls API routes; publishing, duplicate checks, cleanup, backups, and Flickr sync belong in backend services.
- **Use migrations from day one.** Even with SQLite, schema changes go through Alembic or a simple migration runner so upgrades are repeatable.
- **Add `.gitignore` and `.dockerignore`.** Never commit `.env`, database files, originals, thumbnails, derivatives, backups, or logs.
- **Document setup and restore.** `docs/setup.md` covers first-run admin creation, env var generation, watch-folder SMB share. `docs/restore.md` covers DB restore from backup and what's recoverable when the photo volume is lost.

---

## Project Folder Structure

```
framepost/
├── docker-compose.yml
├── BRIEF.md                    ← this file
├── .env.example                ← SECRET_KEY, TOKEN_ENCRYPTION_KEY, FLICKR_API_KEY, FLICKR_API_SECRET, ANTHROPIC_API_KEY
├── .gitignore
├── .dockerignore
├── docs/
│   ├── setup.md                ← first-run admin, env vars, SMB share, Flickr API key registration
│   └── restore.md              ← restore procedure for DB + photo volume backups
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 ← FastAPI app entry, /health endpoint
│   ├── config.py
│   ├── database.py             ← SQLite connection, WAL pragmas, migrations, seed values
│   ├── models.py               ← SQLAlchemy models
│   ├── crypto.py               ← Fernet encrypt/decrypt for tokens
│   ├── admin.py                ← CLI: create-admin, reset-password, generate-encryption-key, backup
│   ├── routes/
│   │   ├── auth.py
│   │   ├── posts.py
│   │   ├── schedule.py
│   │   ├── history.py
│   │   ├── profiles.py
│   │   ├── albums.py
│   │   ├── groups.py
│   │   ├── platforms.py
│   │   ├── health.py           ← /health endpoint logic
│   │   └── config.py
│   ├── services/
│   │   ├── scheduler.py        ← APScheduler: post-fire + cron jobs + heartbeat
│   │   ├── image.py            ← upload, stability, hash, resize, thumbnail
│   │   ├── exif.py             ← piexif extraction
│   │   ├── iptc.py             ← IPTCInfo3 extraction (Lightroom title/caption/keywords)
│   │   ├── ai_tagging.py       ← Claude Vision integration
│   │   ├── duplicate.py        ← hash + machine-tag duplicate detection
│   │   ├── events.py           ← write rows to post_events
│   │   ├── retry.py            ← exponential backoff helper
│   │   ├── platforms/
│   │   │   ├── base.py         ← abstract platform interface
│   │   │   └── flickr.py       ← Flickr (upload, albums, groups, sync)
│   │   ├── flickr_sync.py      ← daily Flickr index refresh
│   │   ├── watcher.py          ← watchdog folder monitor (Phase 2)
│   │   ├── backup.py           ← SQLite hot backup + rotation
│   │   ├── cleanup.py          ← purge expired originals
│   │   └── health.py           ← health check service (writable, free space, etc.)
│   └── data/                   ← OS-disk volume (just the DB)
│       └── framepost.db
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── App.jsx
│       ├── pages/{Login,DraftQueue,Scheduled,Published,Settings}.jsx
│       ├── components/
│       │   ├── Topbar.jsx, ConnectionPill.jsx, StatusBanner.jsx
│       │   ├── DraftCard.jsx, DraftGrid.jsx, MetadataEditor.jsx
│       │   ├── StatsRow.jsx, Calendar.jsx, TimePicker.jsx
│       │   ├── TagProfile.jsx, AlbumPicker.jsx, GroupPicker.jsx
│       │   ├── ExifReadout.jsx, IptcReadout.jsx, ActivityTimeline.jsx
│       │   ├── WatchFolderStatus.jsx, ScheduleOverview.jsx
│       │   ├── HealthPanel.jsx
│       │   └── EmptyState.jsx
│       ├── hooks/{useDrafts,useSchedule,useAlbums,useGroups,useConfig,useHealth}.js
│       └── api/client.js
└── nginx/
    └── nginx.conf              ← reverse proxy + client_max_body_size 200M
```

---

## docker-compose.yml outline

```yaml
services:
  backend:
    build: ./backend
    volumes:
      - ./backend/data:/app/data            # OS disk: SQLite DB
      - /mnt/photo-data:/mnt/photo-data     # mounted volume: photos
    ports:
      - "8000:8000"
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - TOKEN_ENCRYPTION_KEY=${TOKEN_ENCRYPTION_KEY}
      - DATABASE_URL=sqlite:///app/data/framepost.db
      - PHOTO_ROOT=/mnt/photo-data
      - FLICKR_API_KEY=${FLICKR_API_KEY}
      - FLICKR_API_SECRET=${FLICKR_API_SECRET}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    restart: unless-stopped

  worker:
    build: ./backend
    command: python -m services.scheduler
    volumes:
      - ./backend/data:/app/data
      - /mnt/photo-data:/mnt/photo-data
    environment:
      - TOKEN_ENCRYPTION_KEY=${TOKEN_ENCRYPTION_KEY}
      - DATABASE_URL=sqlite:///app/data/framepost.db
      - PHOTO_ROOT=/mnt/photo-data
      - FLICKR_API_KEY=${FLICKR_API_KEY}
      - FLICKR_API_SECRET=${FLICKR_API_SECRET}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    depends_on:
      - backend
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - frontend
      - backend
    restart: unless-stopped
```

Cleanup is an APScheduler cron job inside `worker`, not a separate container.

---

## Design System

### Colour palette
- Teal accent: `#5DCAA5` (logo, progress bar, active states, primary buttons)
- Success/connected: `#1D9E75` / `#0F6E56`
- Flickr badge: `#E6F1FB` bg / `#0C447C` text
- Posted pill: `#E1F5EE` bg / `#085041` text
- Late pill: `#FAEEDA` bg / `#633806` text
- Missed/Failed pill: `#FCEBEB` bg / `#791F1F` text
- Status banner (degraded): `#FAEEDA` bg / `#633806` text
- Status banner (down): `#FCEBEB` bg / `#791F1F` text
- Page background: `#0A0A0A`
- Card background: `#161616`
- Hover surface: `#1F1F1F`

### Typography
- Font: system sans-serif (Inter or similar)
- Weights: 400 regular, 500 medium only
- Sentence case everywhere

### Layout
- Flat surfaces, no gradients, no drop shadows
- 0.5px borders throughout
- Border radius: 8px (elements), 12px (cards)
- Generous whitespace

---

## Security & Operational Notes

### CLI admin commands
A small set of administrative commands runs inside the backend container via `docker compose exec backend python -m admin <command>`:

- `create-admin` — first-run setup. Creates the single admin account interactively. Idempotent: refuses to run if an admin already exists.
- `reset-password` — sets a new password for the admin (when forgotten). Prompts for the new password, hashes with Argon2id, writes to the DB.
- `generate-encryption-key` — outputs a fresh Fernet key for `TOKEN_ENCRYPTION_KEY`. Used during initial setup and for key rotation.
- `backup` — runs an ad-hoc DB backup in addition to the daily scheduled one.

Documented in `docs/setup.md`. There is no public sign-up route or web-based first-run flow — admin setup happens at the shell.

### Token encryption
Flickr OAuth tokens are encrypted at rest using Fernet (AES-128-CBC + HMAC) from the `cryptography` library.

```python
# backend/crypto.py
from cryptography.fernet import Fernet, MultiFernet
import os

# TOKEN_ENCRYPTION_KEY is a comma-separated list of 32-byte URL-safe base64 keys.
# Generate with: python -m admin generate-encryption-key
# Store in .env, NEVER commit, NEVER log.

_keys = [Fernet(k.encode()) for k in os.environ['TOKEN_ENCRYPTION_KEY'].split(',')]
_fernet = MultiFernet(_keys)  # First key encrypts, all keys decrypt

def encrypt_token(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()

def decrypt_token(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
```

Stored as base64 in `platform_credentials.access_token` and `.refresh_token`. Decrypted only at API-call time, never logged. The `key_version` column tracks which key encrypted each row, supporting rotation.

### Authentication and sessions
- Passwords hashed with **Argon2id** using `passlib[argon2]` or `pwdlib`.
- First admin account created via `create-admin` CLI command. After setup, the command refuses to run again unless explicitly told to override.
- Session cookies are `HttpOnly` and `SameSite=Lax`. `Secure=true` only if TLS is later enabled; for LAN-only HTTP, `Secure=false` is documented as expected.
- State-changing requests require CSRF protection.
- Session timeout configurable in Settings → System, backed by `app_config.session_timeout_minutes`.

### API and worker idempotency
- Posting jobs must be idempotent. Before uploading, the worker re-checks local status, `flickr_photo_id`, and the Flickr duplicate cache so a restarted worker does not double-post.
- Every external Flickr API operation logs a `post_events` row with request purpose, result state, attempt number, and sanitized error details.
- Retry uses bounded exponential backoff (5 attempts: 1m, 5m, 15m, 1h, 4h). Permanent validation errors skip retry.

### Time handling
- Store all timestamps in UTC in SQLite.
- Display and schedule times in the configured workspace time zone (`America/Chicago` by default).
- Persist the configured time zone in `app_config.timezone`.

### SQLite operational rules
- WAL mode enforced at every connection open
- Hot backup via `con.backup()` API only — never `cp`/`rsync` of the live `.db` file
- Backups go to `/mnt/photo-data/backup/`
- Retention: 7 daily / 4 weekly / 3 monthly, rotated by the cleanup job
- WAL checkpoint runs after each backup

### Network and access
- LAN-only. Remote access via VPN only — never expose port 80 to the public internet.
- Nginx limits `client_max_body_size` to 200 MB.

### Process and filesystem
- App runs as a dedicated non-root user inside the container.
- The `/mnt/photo-data` mount is owned by the app user, mode 750.
- All queries go through SQLAlchemy parameter binding.

### Data deletion safety
Originals deleted only when ALL of: post status is `posted`, thumbnail file exists on disk, `posted_at` older than retention window. Cleanup job logs every deletion to `post_events` with `event_type='original_purged'`.

### Logging
- Application logs to stdout (captured by Docker)
- Never log: OAuth tokens, SECRET_KEY, TOKEN_ENCRYPTION_KEY, password hashes, raw request bodies containing image data
- Log: post status transitions, Flickr API errors with response codes, cleanup actions, backup successes/failures, retry attempts

---

## Pre-Phase-1 Setup (operator, not Claude Code)

Before Phase 1 development is possible, the operator must:

1. Provision the Ubuntu Server LTS VM (4 vCPU / 8 GB RAM / 80 GB OS disk).
2. Mount the photo storage volume at `/mnt/photo-data` and verify ownership.
3. Install Docker and Docker Compose.
4. Configure VPN access to the VM.
5. Set up an SMB share exposing `/mnt/photo-data/incoming/` to the Lightroom workstation (deferred until Phase 2 if preferred).
6. Register a Flickr API app at https://www.flickr.com/services/apps/create — receive `FLICKR_API_KEY` and `FLICKR_API_SECRET`. (Required for OAuth flow in Phase 3, but worth getting now.)
7. Generate `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY`. The latter via `python -m admin generate-encryption-key` once the backend is built.
8. Obtain `ANTHROPIC_API_KEY` for Phase 5 AI tagging.
9. Populate `.env` from `.env.example` with all the above.

These steps are documented in `docs/setup.md`.

---

## First Claude Code prompt

Once you have this file in your project folder, start Claude Code in that directory and use this as your opening prompt:

```
Read BRIEF.md carefully. Then scaffold the complete FramePost project structure
as described in the "Project Folder Structure" section. Create the folder
hierarchy, docker-compose.yml (services: backend, worker, frontend, nginx —
no separate cleanup container), Dockerfiles, requirements.txt with FastAPI,
SQLAlchemy, Alembic, APScheduler, Pillow, piexif, IPTCInfo3, watchdog,
cryptography, Uvicorn, anthropic, httpx, and passlib[argon2]. Generate
package.json with React, React Router, Axios.

Create the SQLite schema from the "Data Model" section including all tables,
indexes, foreign keys with cascade, and the seed app_config rows from the
"Initial app_config seed values" section. Set up database.py to enforce WAL
mode and the other PRAGMAs on connection. Create crypto.py with Fernet
helpers. Create admin.py with the four CLI commands (create-admin,
reset-password, generate-encryption-key, backup).

Generate a .env.example listing SECRET_KEY, TOKEN_ENCRYPTION_KEY,
FLICKR_API_KEY, FLICKR_API_SECRET, ANTHROPIC_API_KEY, DATABASE_URL,
PHOTO_ROOT. Configure nginx.conf with client_max_body_size 200M. Add
.gitignore and .dockerignore covering .env, *.db, originals/, thumbnails/,
derivatives/, errors/, backup/, and logs. Create docs/setup.md and
docs/restore.md as stubs.

Do not build any features yet — just get the scaffold correct so we can
build Phase 1 on top of it.
```

Then follow the phases in order. One phase at a time, one feature at a time.

---

## Key decisions summary

| Topic | Decision |
|---|---|
| Workflow | Queue-first: Lightroom → watch folder → Draft Queue → schedule → publish |
| Default landing page | Draft Queue |
| Platform | Flickr only — no Instagram in schema or roadmap |
| VM specs | 4 vCPU / 8 GB RAM / 80 GB OS disk |
| Photo storage | Separate mounted volume at `/mnt/photo-data`, 500 GB – 1 TB |
| Database location | OS disk (for WAL performance); backups go to photo volume |
| SQLite mode | WAL, `synchronous=NORMAL`, foreign keys on, mmap 256 MB |
| Large file ceiling | 200 MB upload limit (handles 60–70 MP exports) |
| Pillow safety | `MAX_IMAGE_PIXELS = 200_000_000` (decompression-bomb ceiling preserved) |
| EXIF | Extracted on import, key fields broken out as columns + full blob in `exif_raw` |
| IPTC | Extracted on import, populates draft `title` / `description` / `tags` from Lightroom-written metadata; full blob in `iptc_raw` |
| File metadata | Original filename, byte size, dimensions, hash, error message stored on posts |
| Missed posts | Auto-post if within 24h, flag if older |
| Retry policy | 5 attempts with backoff 1m/5m/15m/1h/4h, then `failed` |
| Disk-full hard stop | 5 GB free minimum, refuse imports below |
| Health endpoint | `GET /health` with worker heartbeat, DB writable, photo volume free, last Flickr success, last backup |
| Status banner | Appears top-of-page when `/health` is degraded or down |
| Thumbnails | Permanent, cover-cropped |
| Originals | Auto-purged after 30d, only when post=`posted` and thumbnail exists |
| Containers | 4 services: backend, worker, frontend, nginx (cleanup folded into worker) |
| First-run admin | CLI `create-admin` command — no public sign-up route |
| Password reset | CLI `reset-password` command |
| Password storage | Argon2id hashes only |
| Token storage | Fernet-encrypted at rest, key version tracked, never logged |
| Backups | SQLite online backup API, 7 daily / 4 weekly / 3 monthly, on photo volume |
| Audit trail | Every post state change written to `post_events` |
| DB integrity | Explicit indexes and foreign-key cascades for child tables |
| Import paths | Watch folder (primary, Phase 2), browser upload (fallback, Phase 1) — same pipeline |
| Local duplicate detection | SHA256 on every imported file |
| Flickr-side duplicate detection | `framepost:sha256=<hash>` machine tag |
| Flickr organization | Albums for browsing, Tags for search, Groups for distribution |
| Group posting | Max 5 default, warn at 8, manual confirmation, per-group submission tracking, retry policy applies |
| Tag profiles | Stackable, global default cannot be deleted |
| Batch scheduling | Smart fill — distribute queue across date range at chosen cadence |
| AI tagging | Opt-in (default off), resized preview by default, full-res only via explicit setting |
| Storage warning | Amber badge at 80%, red at hard-stop |
| Cleanup time | 3am daily (configurable), runs in worker container |
| Time zone | `America/Chicago`, all times stored UTC |
| Studio name | "Darrell Miller Photography" |
| Default privacy | Private |
| Default safety / content type | Safe / Photo |
