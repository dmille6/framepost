"""Queue a photographer's best existing photos into newly configured groups.

Assignments normally materialise at publish time, so 500+ already-published photos
are in none of the 18 groups configured from existing memberships. This queues a
small, curated backfill.

Three deliberate constraints, each load-bearing:

RANKING BY VIEWS, NOT FAVES. Faves cannot rank this catalogue -- 55 posts have 0,
32 have 1, 19 have 2, and the maximum is 6. Sorting by faves returns near-arbitrary
photos. Views spread from 673 to 37 (median), so they can actually order the set.

DIVERSITY CAP. The raw top 20 by views is five frames of one performer and three of
another. Submitting near-duplicates of one set across 18 pools is what a dump looks
like, whatever its quality, so no more than MAX_PER_SUBJECT photos of any one
performer/show are queued.

DRIP DELIVERY. Eight of these groups publish no throttle at all, so the worker would
put all 20 photos into each of them inside a single pass. Flickr permits it; a
moderator watching 20 photos from one account arrive at once does not distinguish
that from spam. Rows are therefore staggered with next_retry_at -- the same field the
throttle deferral uses, so no worker change is needed -- to at most PER_GROUP_PER_DAY
each day, independent of what the group itself allows.

One per group per day is the photographer's own call, and it is the right instinct:
a single photo a day is indistinguishable from ordinary participation, which is what
a moderator is actually judging.

    docker compose exec -T backend python /tmp/backfill_groups.py            # dry run
    docker compose exec -T backend python /tmp/backfill_groups.py --commit
"""
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from database import SessionLocal  # noqa: E402
from models import Post, PostGroup  # noqa: E402
from services import group_routing  # noqa: E402
from sqlalchemy import select, text as sq  # noqa: E402

TOP_N = 20
MAX_PER_SUBJECT = 2
PER_GROUP_PER_DAY = 1


def _subject(post: Post) -> str:
    """Coarse grouping key: the performer or show a title leads with.

    Titles here read "Performer - Show - Venue - Date", so the first segment is the
    subject. Falls back to the whole title, which just means the cap does nothing for
    that row rather than misfiring.
    """
    title = (post.title or "").strip()
    head = re.split(r"\s+[-–]\s+", title)[0] if title else ""
    return re.sub(r"[^a-z0-9]", "", head.lower()) or post.id


def main(commit: bool) -> int:
    db = SessionLocal()
    try:
        ranked = db.execute(sq("""
            SELECT e.post_id, e.views, e.faves
            FROM flickr_engagement e
            JOIN (SELECT post_id, MAX(sampled_at) m
                    FROM flickr_engagement GROUP BY post_id) x
              ON x.post_id = e.post_id AND x.m = e.sampled_at
            JOIN posts p ON p.id = e.post_id
            WHERE p.status IN ('posted','late') AND p.flickr_photo_id IS NOT NULL
            ORDER BY e.views DESC
        """)).all()

        chosen: list[tuple[Post, int, int]] = []
        per_subject: dict[str, int] = {}
        for pid, views, faves in ranked:
            if len(chosen) >= TOP_N:
                break
            post = db.get(Post, pid)
            if post is None:
                continue
            key = _subject(post)
            if per_subject.get(key, 0) >= MAX_PER_SUBJECT:
                continue
            per_subject[key] = per_subject.get(key, 0) + 1
            chosen.append((post, views, faves))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        slot_gap = timedelta(hours=24 / PER_GROUP_PER_DAY)
        queued_per_group: dict[str, int] = {}
        rows: list[PostGroup] = []

        print(f"selected {len(chosen)} photos (cap {MAX_PER_SUBJECT} per subject)\n")
        for post, views, faves in chosen:
            already = {r[0] for r in db.execute(
                select(PostGroup.group_id).where(PostGroup.post_id == post.id)).all()}
            new = [g for g in group_routing.resolve(db, post) if g.id not in already]
            print(f"  {views:>4}v {faves:>2}f  {(post.title or '(untitled)')[:44]:<44} +{len(new)}")
            for g in new:
                n = queued_per_group.get(g.id, 0)
                queued_per_group[g.id] = n + 1
                rows.append(PostGroup(
                    id=uuid.uuid4().hex,
                    post_id=post.id,
                    group_id=g.id,
                    status="pending",
                    # Position within this group's own queue decides the wake-up time,
                    # so every group drips at the same rate regardless of its throttle.
                    # One slot per gap, strictly increasing: with a gap of 24/N hours
                    # that is exactly N per rolling day and no two rows share a slot.
                    next_retry_at=now + slot_gap * n,
                ))

        total = len(rows)
        span = max(queued_per_group.values()) / PER_GROUP_PER_DAY if queued_per_group else 0
        print(f"\n{total} submissions across {len(queued_per_group)} groups")
        print(f"drip: max {PER_GROUP_PER_DAY}/group/day -> ~{span:.1f} days to drain")

        if not commit:
            print("\nDRY RUN -- nothing written. Re-run with --commit.")
            return 0

        for r in rows:
            db.add(r)
        db.commit()
        print(f"\nqueued {total} pending submissions")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main("--commit" in sys.argv))
