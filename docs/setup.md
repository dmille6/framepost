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

Register a Flickr API app at https://www.flickr.com/services/apps/create — paste `FLICKR_API_KEY` and `FLICKR_API_SECRET` into `.env`. (Required from Phase 3 onward.)

`ANTHROPIC_API_KEY` is only required from Phase 5 (AI tagging).

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

Health check: `curl http://<host-ip>/health` should return `{"status":"ok",...}` once the worker has fired its first heartbeat (within ~1 minute of startup).

## 4. SMB share (Phase 2)

Lightroom exports land in `/mnt/photo-data/incoming/`. To expose it to the Lightroom workstation, set up a Samba share pointing at `/mnt/photo-data/incoming/` with write access for the photographer. Detailed steps will be added when Phase 2 ships.

## 5. Restoring from backup

See `docs/restore.md`.
