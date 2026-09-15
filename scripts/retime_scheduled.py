"""Move already-scheduled posts to better times -- and, for the hour, find out which.

Two findings from the September 2026 sweep of the live data, and they deserve very
different treatment, which is the whole point of this script.

CROWDING (act on it). Days carrying one post reached a median 11.3% of followers;
posts on days carrying two or more reached 7.5%. n=17 days against 8. That is not
proof -- crowded days may differ in other ways -- but the direction is consistent,
the mechanism is plausible, and spacing posts out costs nothing whichever way it
turns out. `scatter_max_per_day` still defaults to 2, so the queue keeps producing
the case the data dislikes.

HOUR (test it, don't assume it). Posts after 18:00 reached a median 17.3% against
9.0% for 12:00-17:59 -- but that is FIVE posts against twenty. Five points is an
anecdote with a decimal place. Moving the whole queue to the evening on that basis
would feel decisive and would destroy the only thing that could settle it: there
would be no midday arm left to compare against.

So the default behaviour splits the queue evenly at random between evening
(18:00-21:00) and midday (12:00-15:00). The photographer is posting these
anyway; randomising the hour costs nothing and converts a hunch into an answer in
about six weeks. `--evening` forces the whole queue instead, for when someone has
decided they would rather act on the hunch than measure it -- that is a legitimate
choice, but it should be made on purpose.

Randomisation is seeded from the post id, so a dry run and the `--commit` that
follows it produce identical assignments. Nothing is chosen twice.

    docker compose exec -T backend python /tmp/retime_scheduled.py             # dry run
    docker compose exec -T backend python /tmp/retime_scheduled.py --commit
    docker compose exec -T backend python /tmp/retime_scheduled.py --evening --commit
"""
import hashlib
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

sys.path.insert(0, "/app")

from database import SessionLocal  # noqa: E402
from models import Post  # noqa: E402
from services import events  # noqa: E402
from sqlalchemy import select  # noqa: E402

# The two arms. Ranges rather than a single time so posts on different days do not
# all land on the same minute, which reads as automation to a viewer and to a ranker.
EVENING = (time(18, 0), time(21, 0))
MIDDAY = (time(12, 0), time(15, 0))

MAX_PER_DAY = 1


def _rand(post_id: str, salt: str) -> float:
    """Deterministic 0..1 from the post id.

    Seeded rather than random so `--commit` reproduces exactly what the dry run
    displayed. A second run that silently reshuffled would make the preview a lie.
    """
    h = hashlib.sha256(f"{post_id}:{salt}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _time_in(window: tuple[time, time], post_id: str) -> time:
    start, end = window
    span = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    offset = int(_rand(post_id, "minute") * span)
    total = start.hour * 60 + start.minute + offset
    return time(total // 60, total % 60)


def assign_arms(post_ids: list[str], force_evening: bool) -> dict[str, str]:
    """Split the queue evenly between the two arms.

    Independent coin flips per post would be simpler and worse: on a queue of twelve
    they came out 4/8, and an experiment's power is set by its smaller arm. Ranking
    every post by a seeded hash and halving keeps the assignment deterministic and
    unrelated to anything about the post, while guaranteeing the arms differ by at
    most one.
    """
    if force_evening:
        return {pid: "evening" for pid in post_ids}
    ordered = sorted(post_ids, key=lambda pid: _rand(pid, "arm"))
    half = len(ordered) // 2
    return {pid: ("evening" if i < half else "midday") for i, pid in enumerate(ordered)}


def main(commit: bool, force_evening: bool) -> int:
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Only posts still waiting. Anything already published is history, and a post
    # scheduled in the past is the worker's problem (late/retry), not a timing choice.
    rows = db.execute(
        select(Post)
        .where(Post.status == "pending",
               Post.scheduled_at.is_not(None),
               Post.scheduled_at > now)
        .order_by(Post.scheduled_at)
    ).scalars().all()

    if not rows:
        print("Nothing scheduled in the future. No changes.")
        return 0

    print(f"{len(rows)} scheduled post(s) from "
          f"{rows[0].scheduled_at:%Y-%m-%d} to {rows[-1].scheduled_at:%Y-%m-%d}\n")

    arms = assign_arms([p.id for p in rows], force_evening)

    # --- decrowd: at most MAX_PER_DAY per calendar day -----------------------------
    # Walk in schedule order and push overflow to the next day that has room, so the
    # queue keeps its existing sequence and simply stretches.
    taken: dict[object, int] = defaultdict(int)
    planned: list[tuple[Post, datetime]] = []
    for post in rows:
        day = post.scheduled_at.date()
        while taken[day] >= MAX_PER_DAY:
            day = day + timedelta(days=1)
        taken[day] += 1
        window = EVENING if arms[post.id] == "evening" else MIDDAY
        planned.append((post, datetime.combine(day, _time_in(window, post.id))))

    moved = 0
    arm_counts: dict[str, int] = defaultdict(int)
    for post, when in planned:
        arm = arms[post.id]
        arm_counts[arm] += 1
        if when == post.scheduled_at:
            continue
        moved += 1
        label = (post.title or post.original_filename or post.id)[:38]
        shifted = "  [day moved]" if when.date() != post.scheduled_at.date() else ""
        print(f"  {label:<38} {post.scheduled_at:%a %d %b %H:%M} -> "
              f"{when:%a %d %b %H:%M}  ({arm}){shifted}")

    print(f"\n{moved} post(s) would move.  "
          f"arms: {dict(arm_counts)}  max/day: {MAX_PER_DAY}")

    if not commit:
        print("\nDry run. Re-run with --commit to apply.")
        return 0

    for post, when in planned:
        if when == post.scheduled_at:
            continue
        before = post.scheduled_at
        post.scheduled_at = when
        events.log_event(
            db,
            post_id=post.id,
            event_type="retimed",
            actor="retime_scheduled",
            # The arm is recoverable from the hour alone, but recording it here means
            # the experiment's assignment survives a later manual reschedule.
            details={"from": before.isoformat(), "to": when.isoformat(),
                     "arm": arms[post.id],
                     "forced_evening": force_evening},
        )
    db.commit()
    print(f"\nApplied. {moved} post(s) rescheduled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--commit" in sys.argv, "--evening" in sys.argv))
