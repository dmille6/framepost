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
    # Only 5 empty days left; ask for 8 → 5 from tier 0, 3 from tier 1.
    counts = {d: 1 for d in HORIZON[5:]}
    picks, _ = _pick_days_tiered(HORIZON, counts, needed=8, max_per_day=2)
    assert len(picks) == 8
    tier0 = [d for d in picks if counts.get(d, 0) == 0]
    tier1 = [d for d in picks if counts.get(d, 0) == 1]
    assert len(tier0) == 5 and len(tier1) == 3


def test_respects_ceiling_and_reports_shortfall():
    counts = {d: 2 for d in HORIZON}  # saturated at the 2/day ceiling
    picks, spares = _pick_days_tiered(HORIZON, counts, needed=10, max_per_day=2)
    assert picks == []      # nothing schedulable
    assert spares == []     # and no fallback days either
