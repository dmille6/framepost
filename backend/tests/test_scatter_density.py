"""Tiered scatter density: every day gets one post before any day gets two."""
from datetime import date, timedelta

from routes.schedule import _pick_days_tiered, _stratified


HORIZON = [date(2026, 9, 1) + timedelta(days=i) for i in range(365)]


def test_stratified_spreads_across_range():
    # One pick per segment, so the worst case is two picks at opposite edges of
    # adjacent segments — just under 2x the segment width. Assert that bound holds
    # over many trials rather than a single lucky draw.
    n = 12
    segment = len(HORIZON) / n
    for _ in range(25):
        picks = sorted(_stratified(HORIZON, n))
        assert len(picks) == n == len(set(picks))
        gaps = [(b - a).days for a, b in zip(picks, picks[1:])]
        assert min(gaps) >= 1
        assert max(gaps) <= 2 * segment + 2
        assert sum(gaps) / len(gaps) > segment * 0.6  # genuinely spread, not clustered


def test_tier_zero_only_while_empty_days_exist():
    counts = {HORIZON[i]: 1 for i in range(100)}  # 100 days already have one post
    picks, _ = _pick_days_tiered(HORIZON, counts, needed=20, max_per_day=2)
    assert len(picks) == 20
    # Every pick must be a day that had NOTHING — no doubling up while 265 days are free.
    assert all(counts.get(d, 0) == 0 for d in picks)
    assert len(set(picks)) == 20  # no day picked twice in the same tier


def test_second_pass_starts_only_when_calendar_is_full():
    counts = {d: 1 for d in HORIZON}  # every single day has exactly one post
    picks, _ = _pick_days_tiered(HORIZON, counts, needed=10, max_per_day=2)
    assert len(picks) == 10
    assert all(counts[d] == 1 for d in picks)  # all now going to their 2nd post
    assert len(set(picks)) == 10


def test_mixed_tiers_fill_empties_first_then_spill():
    """Only 5 empty days left; ask for 8 → 5 from tier 0, 3 from tier 1.

    Repeated, because the picker is random: a single trial passed roughly seven times
    in eight while `counts` mutation let a day filled by tier 0 qualify for tier 1 on
    the next pass, taking two photos from one batch while other days sat empty.
    """
    for _ in range(60):
        counts = {d: 1 for d in HORIZON[5:]}
        picks, _ = _pick_days_tiered(HORIZON, counts, needed=8, max_per_day=2)
        assert len(picks) == 8
        assert len(set(picks)) == 8, "a day was given two photos from the same batch"
        tier0 = [d for d in picks if counts.get(d, 0) == 0]
        tier1 = [d for d in picks if counts.get(d, 0) == 1]
        assert len(tier0) == 5 and len(tier1) == 3


def test_respects_ceiling_and_reports_shortfall():
    counts = {d: 2 for d in HORIZON}  # saturated at the 2/day ceiling
    picks, spares = _pick_days_tiered(HORIZON, counts, needed=10, max_per_day=2)
    assert picks == []      # nothing schedulable
    assert spares == []     # and no fallback days either


def test_no_day_doubles_up_while_the_horizon_still_has_empty_days():
    """The rule as the photographer stated it: nothing gets a second photo until every
    day in the year has a first one."""
    for _ in range(40):
        counts = {d: 1 for d in HORIZON[50:]}   # 50 empty days remain
        picks, _ = _pick_days_tiered(HORIZON, counts, needed=50, max_per_day=2)
        assert len(picks) == 50
        assert len(set(picks)) == 50
        # Every pick landed on a day that was empty — none doubled up.
        assert all(counts.get(d, 0) == 0 for d in picks)
