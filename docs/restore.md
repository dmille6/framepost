# FramePost — Restore

## What's where

- **Database** — `backend/data/framepost.db` on the OS disk. Hot backups land in `/mnt/photo-data/backup/framepost-<timestamp>.sqlite`.
- **Originals** — `/mnt/photo-data/originals/`. 30-day retention. Lost originals cannot be recovered after the retention window.
- **Thumbnails** — `/mnt/photo-data/thumbnails/`. Permanent. Acts as the archival "what was posted when" record.
- **OAuth tokens** — encrypted in the `platform_credentials` table using `TOKEN_ENCRYPTION_KEY`. A restored DB requires the same key to decrypt — keep `.env` backed up separately.

## Restore the database

```bash
docker compose down
cp /mnt/photo-data/backup/framepost-<timestamp>.sqlite backend/data/framepost.db
docker compose up -d
```

Never `cp` a live `.db` while the app is running — under WAL the file alone is incomplete. Always restore from the SQLite-backup-API output.

## Restore from a wholesale photo-volume loss

If `/mnt/photo-data/` is lost:

1. Restore the DB from the most recent `framepost-<timestamp>.sqlite` (these are the only DB backups; the photo volume holds them).
2. Originals beyond the retention window are gone. Posts already in `posted` status retain their thumbnails as the archive — but those thumbnails live on the photo volume too, so a full volume loss without an off-host backup is unrecoverable.
3. Re-fetch Flickr-side photo records via the daily `flickr_sync` job — the canonical published copies still live on Flickr.

**Action item:** off-host backup of the photo volume itself is out of scope for v1 but should be considered (rsync/restic to a separate host). Without it, the photo volume is a single point of failure.
