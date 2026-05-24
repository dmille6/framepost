# FramePost

Self-hosted photo scheduler and multi-platform poster for
[Darrell Miller Photography](https://www.flickr.com/photos/darrellmillerphotography/).
Single-user, single-host, runs on a local Ubuntu box behind the photographer's
network perimeter.

## What it does

- **Watch-folder import** from a Lightroom export over a Samba share. Photos
  picked up automatically, IPTC metadata pre-filled, AI tagging via Anthropic
  Haiku + OpenAI GPT-4o-mini for caption and tag suggestions.
- **Scheduled posting to Flickr** with album and group fan-out, EXIF/ICC/XMP
  preservation through Pillow re-encode, and an OAuth 1.0a hand-rolled signer.
- **Multi-platform fan-out** to Bluesky (atproto) and Pixelfed (Mastodon-
  compatible API), with per-platform retry queues and per-post target opt-out.
- **Copy-paste assist tabs** for platforms with restrictive APIs — Instagram
  (caption + hashtags + 1080×1080 / 1080×1350 JPEG with pad/crop) and Reddit
  (title + 2048-px image + subreddit shortcuts).
- **Reels builder** — generates silent 1080×1920 MP4s from up to 10 stills
  using ffmpeg (per-photo 9:16 crop, gentle Ken Burns zoom, optional director
  mode for hero shots). User downloads the MP4 and uploads to Instagram.
- **Activity feed** unifying comments and likes from Flickr / Bluesky /
  Pixelfed, plus manually-tracked Instagram engagement.
- **Title templates**, **tag profiles**, **smart-fill scheduling** (sequential
  and random scatter), **drag-to-schedule** calendar, **bulk edit** across
  drafts.

## Stack

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy 2.0
- **Database**: SQLite (WAL mode) on local OS disk
- **Scheduler**: APScheduler in a sidecar worker process
- **Watch folder**: watchdog with the polling observer
- **Image**: Pillow 11 + piexif + IPTCInfo3 + exiftool (fallback) + ffmpeg
  (for Reels)
- **Frontend**: React 19 + TypeScript + Vite + TanStack Query
- **Crypto**: Fernet via the `cryptography` library for OAuth-token-at-rest
- **Web tier**: nginx (multi-stage Docker build also bundles the frontend)
- **Deployment**: Docker Compose (backend, worker, nginx)

## Layout

```
backend/      FastAPI app, SQLAlchemy models, Alembic migrations,
              services (image, AI, scheduler, watcher, platforms/*)
frontend/     React app — pages, components, API client
nginx/        Multi-stage Dockerfile that builds the frontend and serves it
docs/         Internal documentation
brief.md      The original project brief that this codebase implements
```

## Configuration

Secrets live in `/opt/framepost/.env` (mode 600, gitignored). See
`.env.example` for the shape. The minimum set is:

- `SECRET_KEY` — Starlette session signing
- `TOKEN_ENCRYPTION_KEY` — Fernet key for OAuth-token-at-rest
- `FLICKR_API_KEY` / `FLICKR_API_SECRET`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (optional — disables AI tagging if
  absent)

Per-platform OAuth tokens (Bluesky app password, Pixelfed access token, etc.)
are entered through the in-app Settings → Connections UI and stored encrypted
in the `platform_credentials` table.

## Status

Production single-user deployment since early 2026. Currently in Phase 7+
of the original brief — well past the initial Flickr-only scope, with
multi-platform automation, AI tagging, Reels generation, and the activity
feed all live.

## License

MIT — see [LICENSE](LICENSE).
