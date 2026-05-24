"""Synthesize many backups at various ages, run rotation, see what survives."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.backup import list_backups, rotate_backups

backup_dir = Path("/mnt/photo-data/backup")
for p in backup_dir.glob("framepost-*.sqlite"):
    p.unlink()

# Daily samples for ~120 days. We expect: 7 most recent kept; weekly tier
# picks 4 more across recent weeks (one per ISO-week); monthly tier picks 3
# more across recent months (one per calendar-month). Everything else deleted.
now = datetime.now(timezone.utc).replace(microsecond=0)
hours = list(range(0, 120 * 24, 24))  # one per day for 120 days

for h in hours:
    when = now - timedelta(hours=h)
    fmt = when.strftime("%Y%m%d-%H%M%S")
    p = backup_dir / f"framepost-{fmt}.sqlite"
    p.write_bytes(b"x")
    ts = when.timestamp()
    os.utime(p, (ts, ts))

print(f"created {len(hours)} synthetic backups (120 daily samples)\n")

deleted = rotate_backups()
kept = list_backups()

print(f"deleted: {len(deleted)}")
print(f"kept:    {len(kept)}\n")
print("kept files (newest → oldest):")
for b in kept:
    age_days = (now - b.created_at).days
    print(f"  {b.name}  ({age_days:>3}d old)")
