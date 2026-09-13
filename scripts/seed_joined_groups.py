"""Configure the Flickr groups this account is ALREADY a member of.

flickr.people.getGroups reports 25 memberships; FramePost knew about 3. The other
22 were joined by hand and never submitted to -- no account action is needed to use
them, only configuration. That makes this strictly higher value than joining new
groups, and zero risk: the rules were accepted when they were joined.

Throttles and periods below come from flickr.groups.getInfo and are refreshed by
group_throttle.sync_throttles on the daily job, so they self-correct if an owner
changes them.

Deliberately excluded:
  Critique                            pool submissions disabled (throttle 0/disabled)
  Flickr Friday                       weekly themed challenge; auto-posting is off-theme
  A Camera's Fidelity                 dead, 346 days
  Striptease & Burlesque Female Only  dead, 5343 days

    docker compose exec -T backend python /tmp/seed_joined_groups.py
"""
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from database import SessionLocal  # noqa: E402
from models import Group  # noqa: E402
from sqlalchemy import select  # noqa: E402

# (flickr_group_id, name, category, limit, period, default_enabled, match_tags, notes)
GROUPS = [
    # --- subject-agnostic: every photo qualifies -------------------------
    ("37718508@N00", "Stage Photography",        "performance", None, "day", 1, None, "exact subject match, posts daily"),
    ("26943891@N00", "Rockr Girls",              "performance", None, "day", 1, None, "group states: stage performance pics only"),
    ("51035770480@N01", "New Orleans",           "regional",    10,   "day", 1, None, "active daily; 73% of catalogue is NOLA"),
    ("20074788@N00", "New Orleans Photography",  "regional",    None, "day", 1, None, "active daily"),
    ("2684497@N24", "Flickr Social",             "community",   3,    "day", 1, None, "official Flickr community group"),

    # --- gear: subject-agnostic, reciprocal-faving communities -----------
    ("822590@N23",  "Sony Alpha World",          "gear", None, "day", 1, None, "already a member"),
    ("579871@N23",  "SONY ALPHA CLUB",           "gear", None, "day", 1, None, "already a member"),
    ("836720@N20",  "Sony Photographers",        "gear", 25,   "day", 1, None, "throttle 25/day"),
    ("2774953@N23", "Sony E-mount Shot",         "gear", 5,    "day", 1, None, "throttle 5/day"),
    ("861549@N24",  "Sony Alpha Galleria",       "gear", 2,    "day", 1, None, "throttle 2/day"),
    ("1384461@N24", "Sony Camera Club",          "gear", 2,    "day", 1, None, "moderated pool; throttle 2/day"),

    # --- tag-gated: real but partial coverage ----------------------------
    ("1357288@N22", "Vintage Burlesque and Showgirls", "niche", None, "day", 1,
     "burlesque, showgirl, vintage, burlyq", "18+ group"),
    ("52823174@N00", "Queer Burlesque and Cabaret",    "niche", None, "day", 1,
     "burlesque, cabaret, drag, dragqueen, dragking, boylesque", ""),
    ("461219@N22",  "Circus Photography",             "niche", 10, "day", 1,
     "circus, circusperformer, sideshow, aerial, acrobatics", "throttle 10/day"),
    ("505849@N22",  "DANCE MOVE DANCE",               "niche", None, "day", 1,
     "dance, dancer, bellydance, bellydancer", ""),
    ("71208357@N00", "Dance, dance, dance!",          "niche", None, "day", 1,
     "dance, dancer, bellydance, bellydancer", ""),
    ("20421382@N00", "The Portrait Group",            "niche", 1,  "day", 1,
     "portrait, portraits, headshot", "18+; moderated pool; throttle 1/day"),

    # --- manual only: fit is a human judgement, not a tag -----------------
    ("67042888@N00", "Wonderful Women",               "niche", None, "day", 0, None,
     "opt in per post -- whether a photo suits this group is not something a tag decides"),
]


def main() -> int:
    db = SessionLocal()
    try:
        existing = set(db.execute(select(Group.flickr_group_id)).scalars().all())
        added = 0
        for fid, name, category, limit, period, enabled, match, notes in GROUPS:
            if fid in existing:
                print(f"  skip (already configured): {name}")
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
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
