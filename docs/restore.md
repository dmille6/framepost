# FramePost — Restore

## What's where

- **Database** — `backend/data/framepost.db` on the OS disk. Hot backups land in `/mnt/photo-data/backup/framepost-<timestamp>.sqlite` nightly, rotated 7 daily / 4 weekly / 3 monthly.
- **Originals** — `/mnt/photo-data/originals/`. 30-day retention after posting. Lost originals cannot be recovered after the retention window.
- **Thumbnails** — `/mnt/photo-data/thumbnails/`. Permanent. Acts as the archival "what was posted when" record. The daily orphan scan flags any posted photo whose thumbnail goes missing.
- **Previews** — `/mnt/photo-data/previews/`. Regenerable caches (1600 px).
- **Quarantine** — `/mnt/photo-data/errors/orphans/`. Files the daily scan found on disk with no matching database row (failed imports, interrupted deletes). Review and delete at leisure — nothing puts files here except the reconciler, and it never deletes.
- **OAuth tokens** — encrypted in the `platform_credentials` table using `TOKEN_ENCRYPTION_KEY`. A restored DB requires the same key to decrypt — keep `.env` backed up separately. The Instagram token also dies on any Instagram password change; reconnecting is a dashboard-token paste (see [instagram.md](instagram.md)).

## Restore the database

```bash
docker compose down
cp /mnt/photo-data/backup/framepost-<timestamp>.sqlite backend/data/framepost.db
docker compose up -d
```

Never `cp` a live `.db` while the app is running — under WAL the file alone is incomplete. Always restore from the SQLite-backup-API output.

After a restore, the first daily cleanup's orphan scan will reconcile disk against the restored rows — expect a few quarantined files if the backup predates recent imports.

## Restore from a wholesale photo-volume loss

If `/mnt/photo-data/` is lost:

1. Restore the DB from the most recent `framepost-<timestamp>.sqlite` (these are the only DB backups; the photo volume holds them).
2. Originals beyond the retention window are gone. Posts already in `posted` status retain their thumbnails as the archive — but those thumbnails live on the photo volume too, so a full volume loss without an off-host backup is unrecoverable.
3. Re-fetch Flickr-side photo records via the daily `flickr_sync` job — the canonical published copies still live on Flickr.
4. Hidden Instagram staging variants (`framepost:ig_variant` machine tag on Flickr) are disposable — the daily sweep will clear any that linger.

**Action item (still open):** off-host backup of the photo volume itself — the DB backups and the permanent thumbnail archive both live on it, making it a single point of failure. An rsync/restic job to a second host or bucket is the intended fix.
