"""Daily Flickr index refresh — populates `albums` and `flickr_photos` for offline lookups."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Album, FlickrPhoto
from services.platforms import flickr

log = logging.getLogger("framepost.flickr_sync")


def sync_albums(db: Session) -> int:
    """Pull all photosets for the connected user. Upsert into `albums`. Returns count."""
    root = flickr.rest_call(db, "flickr.photosets.getList")
    container = root.find("photosets")
    if container is None:
        return 0
    now = datetime.now(timezone.utc)
    n = 0
    for ps in container.findall("photoset"):
        flickr_id = ps.get("id")
        if not flickr_id:
            continue
        title = (ps.findtext("title") or "").strip()
        desc = (ps.findtext("description") or "").strip()
        try:
            photo_count = int(ps.get("photos", "0") or "0")
        except ValueError:
            photo_count = 0
        existing = db.execute(
            select(Album).where(Album.flickr_album_id == flickr_id)
        ).scalar_one_or_none()
        if existing:
            existing.name = title
            existing.description = desc
            existing.photo_count = photo_count
            existing.last_synced_at = now
        else:
            db.add(
                Album(
                    id=uuid.uuid4().hex,
                    flickr_album_id=flickr_id,
                    name=title,
                    description=desc,
                    photo_count=photo_count,
                    last_synced_at=now,
                )
            )
        n += 1
    db.commit()
    log.info("synced %d Flickr photosets", n)
    return n


def sync_recent_photos(db: Session, *, per_page: int = 500, max_pages: int = 4) -> int:
    """Pull the user's recently-uploaded photos with machine_tags for Layer-2 duplicate checks."""
    now = datetime.now(timezone.utc)
    total = 0
    for page in range(1, max_pages + 1):
        root = flickr.rest_call(
            db,
            "flickr.people.getPhotos",
            user_id="me",
            extras="machine_tags,date_taken,date_upload,url_o,o_dims",
            per_page=str(per_page),
            page=str(page),
        )
        container = root.find("photos")
        if container is None:
            break
        photos = container.findall("photo")
        if not photos:
            break
        for ph in photos:
            fid = ph.get("id")
            if not fid:
                continue
            machine_tags = ph.get("machine_tags") or ""
            title = ph.get("title") or ""
            url = ph.get("url_o") or ""
            try:
                width = int(ph.get("width_o") or "0")
                height = int(ph.get("height_o") or "0")
            except ValueError:
                width, height = 0, 0
            date_taken = _parse_dt(ph.get("datetaken"))
            date_uploaded = _parse_unix(ph.get("dateupload"))
            existing = db.get(FlickrPhoto, fid)
            if existing:
                existing.title = title
                existing.machine_tags = machine_tags
                existing.date_taken = date_taken
                existing.date_uploaded = date_uploaded
                existing.url = url
                existing.width = width or None
                existing.height = height or None
                existing.last_synced_at = now
            else:
                db.add(
                    FlickrPhoto(
                        flickr_photo_id=fid,
                        title=title,
                        machine_tags=machine_tags,
                        date_taken=date_taken,
                        date_uploaded=date_uploaded,
                        url=url,
                        width=width or None,
                        height=height or None,
                        last_synced_at=now,
                    )
                )
            total += 1
        page_count = int(container.get("pages") or "1")
        if page >= page_count:
            break
    db.commit()
    log.info("synced %d Flickr photos (machine-tag cache)", total)
    return total


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _parse_unix(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None
