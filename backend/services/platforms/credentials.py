"""One credential row per platform, for the life of the install.

Every post_platforms row points at platform_credentials.id. Until 0033 that foreign key
was ON DELETE CASCADE and every connect path "replaced" the connection by deleting the
old row and inserting a fresh uuid — so reconnecting Instagram (which the 60-day token
forces on a schedule) silently deleted every Instagram post_platforms row: published
remote ids and permalinks that engagement sync, analytics and duplicate protection
read, and every pending retry along with them. Disconnect did the same thing on purpose.

The rules this module exists to hold:

  * A reconnect updates the existing row in place. Same id, so history stays attached.
  * "Connected" means the row has an access token. A row without one is either a
    connection still being set up or one the user disconnected — both are "not
    connected" everywhere that asks.
  * Disconnect clears the secrets and keeps the row, marked auth_status="disconnected".
    The history is what the user posted; forgetting the token must not forget that.

The FK is now ON DELETE RESTRICT (0033), so if anything ever tries to delete a
credential that still has history, SQLite refuses rather than cascading.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PlatformCredential

log = logging.getLogger("framepost.credentials")

# auth_status for a row the user disconnected. Distinct from "reauth_required" (which the
# health banner shouts about) because a deliberate disconnect is not a fault.
DISCONNECTED = "disconnected"


def get(db: Session, platform: str) -> PlatformCredential | None:
    return db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == platform)
    ).scalars().first()


def is_connected(row: PlatformCredential | None) -> bool:
    return row is not None and bool(row.access_token)


def upsert(db: Session, platform: str) -> PlatformCredential:
    """The platform's row, creating it on first connect. Never replaces an existing one.

    Caller fills in tokens and commits. A new row starts without a token, i.e. not
    connected, until the caller stores one.
    """
    row = get(db, platform)
    if row is None:
        row = PlatformCredential(id=str(uuid.uuid4()), platform=platform)
        db.add(row)
    return row


def mark_connected(row: PlatformCredential, *, account_name: str | None = None) -> None:
    """A fresh grant was just stored: clear every "needs attention" flag.

    Warns when the account changed. The row is kept either way — its history is still
    the user's — but engagement sync will now be reading the old account's post ids with
    the new account's token, and that is worth a line in the log.
    """
    if account_name is not None:
        if row.account_name and row.account_name != account_name:
            log.warning(
                "%s reconnected as %r (was %r) — existing post history stays attached to "
                "this connection", row.platform, account_name, row.account_name,
            )
        row.account_name = account_name
    row.auth_status, row.auth_error, row.auth_flagged_at = "ok", None, None
    row.last_error = None
    row.connected_at = datetime.now(timezone.utc)


def disconnect(db: Session, platform: str, *, keep_extra: tuple[str, ...] = ()) -> bool:
    """Forget the grant, keep the row and everything that points at it.

    keep_extra names the non-secret extra_json keys worth carrying into a later
    reconnect (the IG user id, a Pinterest default board). Everything else in extra_json
    — app passwords, client secrets, half-finished OAuth state — is dropped.

    Pending post_platforms rows are left pending: with no token, the worker skips them,
    and a reconnect resumes them.
    """
    row = get(db, platform)
    if row is None or (not row.access_token and row.auth_status == DISCONNECTED):
        return False
    try:
        extra = json.loads(row.extra_json or "{}")
    except ValueError:
        extra = {}
    kept = {k: extra[k] for k in keep_extra if k in extra}
    row.access_token = None
    row.refresh_token = None
    row.token_expires = None
    row.extra_json = json.dumps(kept) if kept else None
    row.auth_status, row.auth_error, row.auth_flagged_at = DISCONNECTED, None, None
    db.commit()
    log.info("%s disconnected (credential row kept for post history)", platform)
    return True


def pending_oauth(row: PlatformCredential | None) -> dict | None:
    """In-flight OAuth state for this platform, if a connect was started.

    Lives under extra_json["pending_oauth"] so starting a reconnect never touches the live
    tokens: abandon the flow halfway and the old connection keeps working. Also reads the
    pre-0033 shape (state at the top level with pending=True) so a flow started before
    the upgrade can still complete.
    """
    if row is None:
        return None
    try:
        extra = json.loads(row.extra_json or "{}")
    except ValueError:
        return None
    if isinstance(extra.get("pending_oauth"), dict):
        return extra["pending_oauth"]
    if extra.get("pending"):
        return extra
    return None


def set_pending_oauth(row: PlatformCredential, state: dict) -> None:
    try:
        extra = json.loads(row.extra_json or "{}")
    except ValueError:
        extra = {}
    extra.pop("pending", None)
    extra["pending_oauth"] = state
    row.extra_json = json.dumps(extra)
