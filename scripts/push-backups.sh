#!/usr/bin/env bash
# Ship FramePost's SQLite backups off this box.
#
# Why this exists: backup.py writes to /mnt/photo-data/backup/ and its docstring says that
# location "survives an OS-disk failure". On this deployment it does not — the photo
# directory ended up on the same LVM volume as the OS and the database (a known deviation
# from the brief), so every copy of the data lived on one disk. This pushes them to
# mediastack:/srv/staging, which is a different machine.
#
# Deliberate choices:
#   - No --delete. The local side prunes to 7 daily / 4 weekly / 3 monthly; mirroring that
#     would mean a local wipe propagates to the only off-box copy. The remote keeps its own
#     longer window instead (REMOTE_KEEP_DAYS).
#   - The remote identity is verified before anything is sent. 10.0.0.100 sits inside the
#     pfSense DHCP pool with no reservation, so it can legitimately move to a different
#     machine, and rsyncing a database full of encrypted OAuth tokens to whatever answers
#     at that address is not acceptable. Wrong hostname means abort, not push.
#   - Each file is checked for the SQLite magic string first, so a truncated or half-written
#     backup is never the thing we rely on.
set -uo pipefail

TARGET_HOST="${TARGET_HOST:-10.0.0.100}"
TARGET_USER="${TARGET_USER:-mike}"
EXPECT_HOSTNAME="${EXPECT_HOSTNAME:-mediastack}"
DEST="${DEST:-/srv/staging/framepost-backups}"
SRC="${SRC:-/mnt/photo-data/backup}"
KEY="${KEY:-$HOME/.ssh/id_ed25519_backup}"
REMOTE_KEEP_DAYS="${REMOTE_KEEP_DAYS:-60}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR $*"; exit 1; }

SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15
          -o StrictHostKeyChecking=accept-new)

[ -r "$KEY" ] || die "ssh key $KEY missing or unreadable"
[ -d "$SRC" ] || die "backup source $SRC does not exist"

# --- is the machine answering actually the one we mean? ------------------------------
actual="$(ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" hostname 2>/dev/null)" \
  || die "cannot reach ${TARGET_USER}@${TARGET_HOST} over ssh"
if [ "$actual" != "$EXPECT_HOSTNAME" ]; then
  die "$TARGET_HOST identifies as '$actual', expected '$EXPECT_HOSTNAME' — refusing to send backups"
fi

# --- only ship files that are really SQLite ------------------------------------------
staged=()
while IFS= read -r f; do
  if [ "$(head -c 15 "$f" 2>/dev/null)" = "SQLite format 3" ]; then
    staged+=("$(basename "$f")")
  else
    log "SKIP $(basename "$f") — not a SQLite file (truncated or still being written?)"
  fi
done < <(find "$SRC" -maxdepth 1 -type f -name '*.sqlite' -print | sort)

[ "${#staged[@]}" -gt 0 ] || die "no valid SQLite backups found in $SRC"

ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" "mkdir -p '$DEST'" \
  || die "could not create $DEST on $EXPECT_HOSTNAME"

printf '%s\n' "${staged[@]}" \
  | rsync -a --files-from=- --checksum \
      -e "ssh ${SSH_OPTS[*]}" \
      "$SRC/" "${TARGET_USER}@${TARGET_HOST}:$DEST/" \
  || die "rsync failed"

log "pushed ${#staged[@]} backup file(s) to ${EXPECT_HOSTNAME}:${DEST}"

# --- prune the remote on its own, longer schedule ------------------------------------
pruned="$(ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
  "find '$DEST' -maxdepth 1 -type f -name 'framepost-*.sqlite' -mtime +$REMOTE_KEEP_DAYS -print -delete | wc -l")"
[ "${pruned:-0}" -gt 0 ] && log "pruned $pruned remote backup(s) older than ${REMOTE_KEEP_DAYS}d"

remote_count="$(ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
  "ls -1 '$DEST'/framepost-*.sqlite 2>/dev/null | wc -l")"
newest="$(ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
  "ls -1t '$DEST'/framepost-*.sqlite 2>/dev/null | head -1 | xargs -r basename")"
log "OK ${EXPECT_HOSTNAME}:${DEST} now holds ${remote_count} backup(s), newest ${newest:-none}"
