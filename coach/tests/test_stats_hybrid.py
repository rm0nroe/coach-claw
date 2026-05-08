"""stats.py — hybrid ELO math.

Regression guard for the 'ELO stopped moving' symptom: level index +
level-up detection use integer `lifetime + session // 10` (no phantom
level-ups), while the ELO within-level slide uses float
`lifetime + session / 10` (rating nudges as raw XP accrues).
"""
from __future__ import annotations

import pytest

import stats
from stats import _compute_hybrid, compute_for_render


@pytest.fixture(autouse=True)
def _hermetic_stats_globals(monkeypatch):
    """Pin stats.py module globals to the canonical baseline (craft
    ladder + 1000-2800 ELO range) for the duration of these tests.

    `stats.LEVELS`, `stats.ELO_MIN`, `stats.ELO_MAX` are populated at
    import time from `~/.claude/coach/.user_config.json` (see
    `stats._load_runtime_config`). Without this fixture, a user who
    has run `/config theme <other>` or `/config elo <m> <M>` would see
    these baseline-pinning tests fail on the installed-copy test
    workflow advertised in CLAUDE.md, because the level names + ELO
    interpolation would be reading their live preferences. Tests
    against `_compute_hybrid` are about the math, not the user's
    chosen ladder — force defaults here."""
    monkeypatch.setattr(stats, "LEVELS", stats._build_level_ladder())
    monkeypatch.setattr(stats, "ELO_MIN", 1000)
    monkeypatch.setattr(stats, "ELO_MAX", 2800)


def test_level_stays_stable_under_session_xp():
    """Lifetime=4 (L2 Iterator), session 0→15: level should NOT change."""
    names = {_compute_hybrid(4, s)["name"] for s in range(0, 16)}
    assert names == {"Iterator"}


def test_elo_increases_monotonically_across_session():
    """ELO must be non-decreasing as raw session XP accrues within a level."""
    prev = -1
    for raw in range(0, 16):
        elo = _compute_hybrid(4, raw)["elo"]
        assert elo >= prev, f"ELO regressed at raw={raw}: {elo} < {prev}"
        prev = elo


def test_elo_moves_with_every_raw_xp_in_a_level():
    """The whole point of the hybrid: ELO should not be frozen at session=0
    value. It must actually tick up between raw=0 and raw=15."""
    at_zero = _compute_hybrid(4, 0)["elo"]
    at_fifteen = _compute_hybrid(4, 15)["elo"]
    assert at_fifteen > at_zero
    assert at_fifteen - at_zero >= 5   # meaningful movement, not just rounding


def test_level_up_fires_only_at_bank_boundary():
    """Lifetime=7 (L2, threshold to L3 is 8). Raw session 0-9 should stay L2
    (bank_gain=0). Raw 10+ should cross to L3 (bank_gain=1 → level_xp=8)."""
    for raw in range(0, 10):
        assert _compute_hybrid(7, raw)["name"] == "Iterator"
    for raw in range(10, 16):
        assert _compute_hybrid(7, raw)["name"] == "Builder"


def test_no_phantom_level_up_at_sub_bank_session_xp():
    """With lifetime=7 and raw=9, progress_xp=7.9 crosses L3 threshold (8)
    as a float, but level_xp=7 does NOT. Level name must stay Iterator so
    the user doesn't see a level they're about to fall back from."""
    h = _compute_hybrid(7, 9)
    assert h["name"] == "Iterator"
    assert h["level_xp"] == 7
    assert h["progress_xp"] == pytest.approx(7.9)


def test_sigil_pct_glides_live_within_session():
    """Sigil pct derives from progress_xp (float), not level_xp — so it
    should change for sub-bank raw XP movements even when level_xp is fixed."""
    a = _compute_hybrid(4, 2)["sigil_pct"]
    b = _compute_hybrid(4, 8)["sigil_pct"]
    assert b > a


def test_zero_session_matches_pure_lifetime_baseline():
    """Sanity: session=0 ⇒ level_xp == progress_xp == lifetime."""
    h = _compute_hybrid(42, 0)
    assert h["level_xp"] == 42
    assert h["progress_xp"] == 42


def test_hybrid_renders_expected_elo_for_user_baseline():
    """Baseline lock: ◆ Ⅱ 1044 Iterator with session=0 should map to L2
    Iterator at lifetime=4. Pins the formula so it doesn't drift."""
    h = _compute_hybrid(4, 0)
    assert h["name"] == "Iterator"
    assert h["elo"] == 1044


# -----------------------------------------------------------------------------
# compute_for_render — public render-side helper consumed by the military
# theme's rank line and any future banner that needs idx + name + elo + roman
# + medal_count in one call. Medal count = min(5, idx // 4 + 1) — pins the
# rank-ribbon scaling.

@pytest.mark.parametrize(
    "lifetime,expected_idx,expected_name,expected_roman,expected_medals,expected_next",
    [
        (   0,  0, "Drafter",   "Ⅰ",   1,   3),  # L1 — first rung
        (  15,  3, "Shipper",   "Ⅳ",   1,  25),  # L4 — last 1-medal rank
        (  25,  4, "Craftsman", "Ⅴ",   2,  40),  # L5 — first 2-medal rank
        (  90,  7, "Sensei",    "Ⅷ",   2, 125),  # L8 — last 2-medal rank
        ( 125,  8, "Luminary",  "Ⅸ",   3, 165),  # L9 — first 3-medal rank
        ( 510, 15, "Prodigy",   "ⅩⅥ",  4, 585),  # L16 — last 4-medal rank
        ( 585, 16, "Visionary", "ⅩⅦ",  5, 665),  # L17 — first 5-medal rank (cap)
        ( 840, 19, "Paragon",   "ⅩⅩ",  5, 935),  # L20 — still capped at 5
    ],
)
def test_compute_for_render_breakpoints(
    lifetime, expected_idx, expected_name, expected_roman, expected_medals,
    expected_next,
):
    out = compute_for_render(lifetime, 0)
    assert out["idx"] == expected_idx
    assert out["name"] == expected_name
    assert out["roman"] == expected_roman
    assert out["medal_count"] == expected_medals
    assert out["next_xp"] == expected_next
    # ELO must agree with _compute_hybrid for the same inputs (single source
    # of truth — compute_for_render is just a wrapper).
    assert out["elo"] == _compute_hybrid(lifetime, 0)["elo"]


def test_compute_for_render_session_xp_affects_elo_not_medal():
    """Within-level session XP slides ELO but never changes medal_count
    (which is purely a function of level idx, not float progress_xp)."""
    a = compute_for_render(4, 0)
    b = compute_for_render(4, 15)
    assert a["medal_count"] == b["medal_count"]  # same level
    assert a["idx"] == b["idx"]
    assert b["elo"] >= a["elo"]  # session XP nudges rating
