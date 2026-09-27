"""The two fields the Shoots view needs from /api/schedule.

The queue groups by shoot rather than by publish date, because a decrowded queue sits at
about one post per day and a 354-post queue therefore spans thirteen months — no month page
can show a shoot, and the flat list is one 354-row scroll.

Grouping keys off `original_filename` ("... (69 of 69).jpg"), which the endpoint already
returned. These two are what got added: `captured_at` is the fallback key for exports with
no counter in the name, and `show` labels a group whose members agree on one. If either
silently stops being serialised, the view degrades quietly — every capture-date group
collapses into "Unmatched" and every label falls back to a raw filename — so it is pinned
here rather than noticed later.
"""
from datetime import datetime, timedelta, timezone

from models import Post, User
from routes.schedule import list_scheduled


class _User:
    username = "tester"


def _post(db, *, filename, captured=None, show=None, when_days=3):
    p = Post(
        id=f"p{abs(hash((filename, show, when_days))) % 10**12}",
        original_filename=filename,
        captured_at=captured,
        show=show,
        status="pending",
        scheduled_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(days=when_days),
    )
    db.add(p)
    return p


def test_show_and_captured_at_are_serialised(db):
    shot = datetime(2022, 12, 18, 21, 30)
    _post(db, filename="2026-TeaserFest-Varitease (69 of 69).jpg", show="Varitease")
    _post(db, filename="DSC_0042.jpg", captured=shot, show="Worship", when_days=4)
    db.commit()

    items = list_scheduled(db=db, _user=_User(), range_from=None, range_to=None)
    by_name = {i.original_filename: i for i in items}

    assert by_name["2026-TeaserFest-Varitease (69 of 69).jpg"].show == "Varitease"
    # captured_at is what keys a shoot when the filename carries no "(N of M)" counter.
    assert by_name["DSC_0042.jpg"].captured_at == shot
    assert by_name["DSC_0042.jpg"].show == "Worship"


def test_the_grouping_key_itself_is_still_returned(db):
    """original_filename is the actual key. Losing it would break grouping outright, not
    just its labels."""
    _post(db, filename="2026-Aug-FreakshowPeepShow-Show (124 of 136).jpg")
    db.commit()
    items = list_scheduled(db=db, _user=_User(), range_from=None, range_to=None)
    assert items[0].original_filename == "2026-Aug-FreakshowPeepShow-Show (124 of 136).jpg"


def test_absent_metadata_serialises_as_none_not_an_error(db):
    """Most posts have neither field — 300 of 461 pending had no show on the live queue.
    They must come back as None so the view falls back to the filename key."""
    _post(db, filename="2024 - Huntsville - Venardos Circus  (28 of 316).jpg")
    db.commit()
    items = list_scheduled(db=db, _user=_User(), range_from=None, range_to=None)
    assert items[0].show is None
    assert items[0].captured_at is None
