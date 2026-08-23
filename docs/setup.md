# FramePost — Setup

## 1. Prerequisites
- Ubuntu host with Docker + Docker Compose installed
- `/mnt/photo-data/` exists and is writable by the user running compose (UID 1000)
- LAN/VPN access to port 80 of the host

## 2. Environment

Copy `.env.example` to `.env` and fill in:

```bash
cp .env.example .env
chmod 600 .env
```

Generate `SECRET_KEY` (used for session signing):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Generate `TOKEN_ENCRYPTION_KEY` (Fernet key for OAuth tokens at rest):

```bash
docker compose run --rm backend python -m admin generate-encryption-key
```

Paste both into `.env`.

Register a Flickr API app at https://www.flickr.com/services/apps/create —
paste `FLICKR_API_KEY` and `FLICKR_API_SECRET` into `.env`. When authorizing
the Flickr connection in Settings, the app requests **delete** permissions —
needed by the re-post action and the Instagram staging-variant cleanup.

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are optional — without them AI
tagging and the background alt-text sweep stay idle.

## 3. First-run

```bash
# Build images
docker compose build

# Apply schema (runs Alembic, creates SQLite DB at backend/data/framepost.db)
docker compose run --rm backend alembic upgrade head

# Create the single admin account
docker compose run --rm backend python -m admin create-admin

# Start everything
docker compose up -d
```

The app is reachable at `http://<host-ip>/` over your LAN/VPN.

Health check: `curl http://<host-ip>/health` should return `{"status":"ok",...}`
once the worker has fired its first heartbeat (within ~1 minute of startup).
The payload includes `platform_warnings` — token-expiry and connection
problems surface there and in the in-app banner.

## 4. Connect platforms

All connections live in **Settings → Platforms** and are stored encrypted:

- **Flickr** — OAuth browser flow (required first; everything else fans out
  after the Flickr post)
- **Bluesky** — handle + app password
- **Pixelfed** — instance URL, OAuth flow
- **Pinterest** — OAuth flow, pick a default board
- **Instagram** — paste a long-lived token from your Meta app; the full
  walkthrough (with the tester-role gotchas) is in [instagram.md](instagram.md)

Check **Settings → General** for timezone, default publish time, **default
privacy** (applies to every import), schedule fuzz, and retention windows.

## 5. SMB share

Lightroom exports land in `/mnt/photo-data/incoming/`. Expose that directory
to the Lightroom workstation as a Samba share with write access; the watcher
polls every 5 s and waits for files to finish writing before importing.

## 6. Tests

```bash
docker compose exec backend python -m pytest tests/ -q
```

Covers scheduling math (stratified scatter, learned hours, fuzz), caption
building (title dedupe, hashtag legality), and Instagram crop geometry.

## 7. Restoring from backup

See [restore.md](restore.md).
