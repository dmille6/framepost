"""Seed the Flickr groups roster with active, on-genre pools.

Idempotent: matches on flickr_group_id, inserts only what's missing, never
touches an existing row.

Run AFTER joining the groups on Flickr -- a submission to a group you don't
belong to fails permanently. Run `sync_throttles` afterwards (or just wait for
the daily job): the limits below are what Flickr published when this list was
built, and the group owner can change them at any time.

    docker compose exec -T backend python /tmp/seed_groups.py

Selection criteria: every group here posted within the last ~8 days. Membership
count was deliberately NOT the filter -- "LIVE MUSIC" has 28k members and has
been dead for 5,233 days, and the obvious "Burlesque" group died in 2018.
"""
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from database import SessionLocal  # noqa: E402
from models import Group  # noqa: E402
from sqlalchemy import select  # noqa: E402

# (flickr_group_id, name, category, limit, period, default_enabled, match_tags, notes)
#
# default_enabled marks a group as automatic; match_tags then narrows it.
# A group with default_enabled=1 and no match_tags takes every post -- correct
# for the technique and gear pools, which are subject-agnostic. The music pools
# carry match_tags because only ~14% of the catalogue is tagged concert or
# livemusic, and bulk-submitting burlesque to a concert pool is how a moderator
# removes you.
GROUPS = [
    # --- subject-agnostic: technique and gear, fit every photo ------------
    ("20843169@N00", "Low light photography",          "lowlight", None, "day",   1, None, "10.1k members"),
    ("813587@N21",   "The Available Light Gang",       "lowlight", None, "day",   1, None, "535 members"),
    ("656594@N20",   "Available Darkness",             "lowlight", None, "day",   1, None, "546 members"),
    ("2738011@N23",  "Low Light And Night Photography","lowlight", 5,    "day",   0, None, "18+ group; opt in per post"),
    ("766351@N21",   "SONY ALPHA: Amateur to Advanced","gear",     5,    "day",   1, None, "11k members"),
    ("822590@N23",   "Sony Alpha World",               "gear",     None, "day",   1, None, "9.4k members"),
    ("579871@N23",   "SONY ALPHA CLUB",                "gear",     None, "day",   1, None, "8.4k members"),
    ("925860@N22",   "Sony Alpha Community",           "gear",     None, "day",   1, None, "5k members"),

    # --- exact subject fit: 517/518 posts carry a stage tag ---------------
    ("1278126@N25",  "Stage photography:",             "performance", None, "day", 1, None, "small but exact match, posts daily"),
    ("93564694@N00", "Theatre",                        "performance", None, "day", 1, None, "472 members"),
    ("537270@N21",   "(Acting) The Action of Theatre", "performance", 25,  "day", 1, None, "334 members"),

    # --- tag-gated: real but partial coverage -----------------------------
    ("1135153@N23",  "circus love",                    "niche", None, "day",   1, "circus", "136 posts tagged circus"),
    ("34995731@N00", "Street Performers",              "niche", 3,    "day",   1, "streetperformer, busker", "narrow by design"),
    ("877178@N20",   "Pinup Artist and Models",        "niche", None, "day",   1, "pinup", "4 posts tagged pinup"),
    ("575198@N25",   "That's Pinup!",                  "niche", 5,    "day",   0, "pinup", "18+; opt in per post"),

    # --- music pools: ~14% of the catalogue, gated ------------------------
    ("29928242@N00", "Live Music Photography",         "music", 13,   "day",   1, "concert, livemusic, band", "18.8k members"),
    ("77055362@N00", "Concert Photographer",           "music", None, "day",   1, "concert, livemusic, band", "13.6k members"),
    ("86111082@N00", "Concerts",                       "music", None, "day",   1, "concert, livemusic, band", "17.1k members"),
    ("83934753@N00", "Music Photography",              "music", None, "day",   1, "concert, livemusic, band", "16.4k members"),
    ("351309@N21",   "Music Photography goes Digital", "music", None, "day",   1, "concert, livemusic, band", "500 members"),
    ("54089018@N00", "Concert Photography",            "music", 1,    "week",  1, "concert, livemusic, band", "37.8k members; 1 per WEEK"),
    ("41181764@N00", "[DMS] only DYNAMIC MUSIC SHOTS", "music", 30,   "month", 1, "concert, livemusic, band", "30 per month"),
]


def main() -> int:
    db = SessionLocal()
    try:
        existing = set(db.execute(select(Group.flickr_group_id)).scalars().all())
        added = 0
        for fid, name, category, limit, period, enabled, match, notes in GROUPS:
            if fid in existing:
                print(f"  skip (already present): {name}")
                continue
            db.add(Group(
                id=uuid.uuid4().hex,
                flickr_group_id=fid,
                name=name,
                category=category,
                daily_limit=limit,
                limit_period=period,
                match_tags=match,
                content_notes=notes,
                no_watermark=0,
                default_enabled=enabled,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            added += 1
            print(f"  + {category:<12} {name}")
        db.commit()
        total = db.execute(select(Group)).scalars().all()
        print(f"\nadded {added}; roster is now {len(total)} groups")
        print("next: run sync_throttles to replace these limits with Flickr's live values")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
