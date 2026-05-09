"""Per-theme bespoke <coach-celebrate> shapes.

Pins literal-text contracts for the five bespoke themes (forge, ocean,
skyrim, military, hacker) and the regression guard for the seven default
themes (which must produce None and fall through to the default renderer).

Verbatim-render contract: every banner string is fully-resolved Python
text. These tests pin substrings of that text so a refactor that flips
emoji or swaps vocabulary fails immediately.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import banner_themes
import stats
from banner_themes import (
    BESPOKE_THEMES,
    render_celebrate_for_theme,
    _render_verb_style,
    _format_window_phrase,
    SPECS,
)


@pytest.fixture(autouse=True)
def _hermetic_stats_globals(monkeypatch):
    """Pin stats.LEVELS to the canonical craft ladder for the duration of
    these tests. Mirrors the autouse pattern in test_stats_hybrid.py:16 —
    without this, a user who has run `/config theme <other>` would see
    these tests fail because L9 threshold + L8 name come from the live
    user config, not the hardcoded defaults the locked shapes assume."""
    monkeypatch.setattr(stats, "LEVELS", stats._build_level_ladder())
    monkeypatch.setattr(stats, "ELO_MIN", 1000)
    monkeypatch.setattr(stats, "ELO_MAX", 2800)


# Reference clock for window-phrase tests: 2026-05-07 17:44 UTC.
NOW = datetime(2026, 5, 7, 17, 44, tzinfo=timezone.utc)
YESTERDAY = datetime(2026, 5, 6, 19, 0, tzinfo=timezone.utc)
TWO_DAYS_AGO = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# Window phrasing — every theme that includes a "since X" header consumes
# this dict, so the keys + relative-day logic are pinned here.

def test_window_phrase_yesterday():
    p = _format_window_phrase(NOW, YESTERDAY)
    assert p["relative"] == "yesterday"
    assert p["iso_date"] == "2026-05-06"
    assert p["iso_datetime"] == "2026-05-06 19:00"
    assert p["now_iso_date"] == "2026-05-07"
    assert p["now_zulu_time"] == "1744Z"


def test_window_phrase_two_days_ago_uses_iso():
    p = _format_window_phrase(NOW, TWO_DAYS_AGO)
    assert p["relative"] == "2026-05-05"
    assert p["iso_date"] == "2026-05-05"


def test_window_phrase_same_day():
    same = NOW.replace(hour=8)
    p = _format_window_phrase(NOW, same)
    assert p["relative"] == "earlier today"


def test_window_phrase_no_oldest():
    p = _format_window_phrase(NOW, None)
    assert p["relative"] == "earlier"
    assert p["iso_date"] == "2026-05-07"


# -----------------------------------------------------------------------------
# Bespoke / default theme set guards.

def test_bespoke_themes_set_is_exactly_five():
    """If you add or remove a bespoke theme, this test fails — forces a
    conscious choice about what shipping a 6th bespoke shape looks like."""
    assert BESPOKE_THEMES == frozenset({
        "forge", "ocean", "skyrim", "military", "hacker",
    })


@pytest.mark.parametrize("theme", [
    "craft", "cosmic", "marvel", "dc", "finalfantasy", "lotr", "starwars",
])
def test_default_themes_return_none(theme):
    """The seven default themes must defer to the hook's existing renderer.
    None signals 'I'm not handling this — fall through.'"""
    out = render_celebrate_for_theme(
        theme,
        streak_rewards=[{
            "id": "x", "name": "x", "streak": 3, "target": 5,
            "xp_awarded": 1, "direction": "negative",
        }],
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is None


# -----------------------------------------------------------------------------
# Ocean theme — first verb-style implementation. Pins:
#   header glyph + label + window phrasing
#   row meter + name + verb + arrow+xp shape
#   level-up footer with theme-aware level name + next_xp threshold

def _ocean_streak_fixture():
    return [
        {"id": "safe-git-hygiene", "name": "safe git hygiene",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "effective-skill-use", "name": "effective skill use",
         "streak": 3, "target": 5, "xp_awarded": 1, "direction": "positive"},
        {"id": "good-debugging", "name": "good debugging",
         "streak": 2, "target": 5, "xp_awarded": 1, "direction": "positive"},
        {"id": "heavy-subagent-delegation", "name": "heavy subagent delegation",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
        {"id": "commit-without-testing", "name": "commit without testing",
         "streak": 3, "target": 5, "xp_awarded": 1, "direction": "negative"},
    ]


def test_ocean_header_uses_lobster_and_relative_phrase():
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "> 🦞  Tide turned · since yesterday" in out


def test_ocean_streak_row_positive_direction():
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    # Positive-direction row: rising tide + ↑arrow.
    assert "≋≋≋≋·  safe git hygiene" in out
    assert "rising tide" in out
    assert "↑2" in out


def test_ocean_streak_row_negative_direction():
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    # Negative-direction row: ebbing + ↓arrow.
    assert "heavy subagent delegation" in out
    assert "ebbing" in out
    assert "↓2" in out


def test_ocean_meter_glyphs_match_locked_shape():
    """Meter uses ≋ filled and · empty per the locked design."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=[{
            "id": "x", "name": "x", "streak": 4, "target": 5,
            "xp_awarded": 2, "direction": "positive",
        }],
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert "≋≋≋≋·" in out
    # Default-theme meter must NOT leak in.
    assert "●" not in out
    assert "▰" not in out


def test_ocean_levelup_footer_uses_theme_level_name_and_next_xp():
    """Level-up at L8 should: (a) pull L8 name from active LEVELS ladder
    (Sensei in the canonical craft baseline), (b) use the L9 threshold
    (125) for `next fathom at X XP` — NOT the just-crossed L8 threshold."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=[],
        levelup={"to": "Sensei", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "🌊Deep Water🌊" in out
    assert "Sensei (L8)" in out
    assert "next fathom at 125 XP" in out
    # Glyph at the front of the footer.
    assert "> ⚓ 🌊Deep Water🌊" in out


def test_ocean_full_block_streak_plus_levelup():
    """Composed: header, blank, rows, blank, levelup. All in one block."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup={"to": "Sensei", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert out.startswith("<coach-celebrate>")
    assert out.endswith("</coach-celebrate>")
    # Header before rows.
    header_pos = out.index("> 🦞  Tide turned")
    row_pos = out.index("safe git hygiene")
    levelup_pos = out.index("🌊Deep Water🌊")
    assert header_pos < row_pos < levelup_pos


def test_ocean_returns_none_when_nothing_to_render():
    """No streak rewards, no levelup, no grads, no regs → None.
    Caller must not emit an empty <coach-celebrate>."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=[],
        levelup=None,
        now=NOW,
        streak_oldest=None,
    )
    assert out is None


def test_ocean_grads_render_between_streak_and_levelup():
    """Pre-rendered graduation block lands BETWEEN the bespoke streak
    section and the bespoke levelup footer — the order locked in the plan."""
    grads_default = "> 🎓⚡️ **GRADUATED: skipped search tools**  `+5 XP`\n> `🔴🔴🔴🔴🔴` — 5 clean Coach insights runs in a row — weakness retired."
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup={"to": "Sensei", "to_idx": 7, "xp_at_levelup": 90},
        grads_block=grads_default,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    streak_pos = out.index("safe git hygiene")
    grad_pos = out.index("GRADUATED: skipped search tools")
    levelup_pos = out.index("🌊Deep Water🌊")
    assert streak_pos < grad_pos < levelup_pos


def test_ocean_slug_does_not_leak():
    """Slugs (kebab-case ids) must never appear in the rendered banner —
    only the human-readable name."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert "heavy-subagent-delegation" not in out
    assert "safe-git-hygiene" not in out


def test_ocean_levelup_only_no_streak_section_emitted():
    """If only a levelup is queued, header + rows are skipped — output
    is just the level-up footer (no orphan 'Tide turned' header)."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=[],
        levelup={"to": "Reefer", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "Tide turned" not in out
    assert "🌊Deep Water🌊" in out


# -----------------------------------------------------------------------------
# Forge theme — second verb-style implementation. Validates SPECS scales.
#   header: ⚒ The Anvil · {oldest} → now
#   verbs:  tempering / quenching
#   meter:  ▰▱
#   footer: ✨ **{name}** (L{n}) forged anew · next heat at X XP

def _forge_streak_fixture():
    return [
        {"id": "safe-git-hygiene", "name": "safe git hygiene",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "heavy-subagent-delegation", "name": "heavy subagent delegation",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
    ]


def test_forge_header_uses_anvil_glyph_and_iso_window():
    """Forge header uses ISO-date arrow window (not 'since X')."""
    out = render_celebrate_for_theme(
        "forge",
        streak_rewards=_forge_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "> ⚒  The Anvil · 2026-05-06 → now" in out


def test_forge_streak_row_verbs_and_meter():
    out = render_celebrate_for_theme(
        "forge",
        streak_rewards=_forge_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    # Positive direction → tempering verb + ↑arrow.
    assert "▰▰▰▰▱" in out
    assert "tempering" in out
    assert "↑2" in out
    # Negative direction → quenching verb + ↓arrow.
    assert "quenching" in out
    assert "↓2" in out
    # Ocean glyphs must NOT leak into forge.
    assert "≋" not in out
    assert "rising tide" not in out


def test_forge_levelup_uses_theme_level_name_and_l9_threshold():
    """L8 levelup → forge ladder name (Mastersmith), L9 threshold (125 XP).
    The user's mockup wrote '90 XP' but that's the L8 threshold; the
    correct render is the threshold the user is heading toward (L9)."""
    out = render_celebrate_for_theme(
        "forge",
        streak_rewards=[],
        levelup={"to": "Mastersmith", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "✨" in out
    assert "**Mastersmith** (L8) forged anew" in out
    assert "next heat at 125 XP" in out


def test_forge_full_block_streak_plus_levelup_ordering():
    out = render_celebrate_for_theme(
        "forge",
        streak_rewards=_forge_streak_fixture(),
        levelup={"to": "Mastersmith", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    header_pos = out.index("⚒  The Anvil")
    row_pos = out.index("▰▰▰▰▱  safe git hygiene")
    levelup_pos = out.index("forged anew")
    assert header_pos < row_pos < levelup_pos


def test_forge_does_not_use_ocean_footer_glyph():
    """Each theme has its own levelup glyph — guards against accidental
    SPEC merge regressions."""
    out = render_celebrate_for_theme(
        "forge",
        streak_rewards=[],
        levelup={"to": "Mastersmith", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    # Forge uses ✨; ocean uses ⚓ + 🌊Deep Water🌊.
    assert "🌊" not in out
    assert "Deep Water" not in out
    assert "Tide turned" not in out


# -----------------------------------------------------------------------------
# Skyrim theme — third verb-style implementation. Adds glyph fallback:
# header + meter use ⚔ (U+2694) when terminal supports it, ✕ otherwise.
#   header: ⚔ Saga · since {iso_date}
#   verbs:  oath kept / curse fades
#   meter:  ⚔· (or ✕· in fallback)
#   footer: ⚜ **{name}** (L{n}) — next title at X XP

def _skyrim_streak_fixture():
    return [
        {"id": "safe-git-hygiene", "name": "safe git hygiene",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "heavy-subagent-delegation", "name": "heavy subagent delegation",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
    ]


def test_skyrim_header_uses_dual_blade_when_supported():
    out = render_celebrate_for_theme(
        "skyrim",
        streak_rewards=_skyrim_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
        dual_blade_supported=True,
    )
    assert out is not None
    assert "> ⚔  Saga · since 2026-05-06" in out
    # Meter rows use ⚔ filled.
    assert "⚔⚔⚔⚔·" in out


def test_skyrim_falls_back_to_x_glyph_when_dual_blade_unsupported():
    """When supports_dual_blade() returns False, both header glyph AND
    meter glyph swap to ✕ — the fallback is global to the theme."""
    out = render_celebrate_for_theme(
        "skyrim",
        streak_rewards=_skyrim_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
        dual_blade_supported=False,
    )
    assert out is not None
    assert "> ✕  Saga · since 2026-05-06" in out
    assert "✕✕✕✕·" in out
    # ⚔ must NOT appear anywhere when fallback is active.
    assert "⚔" not in out


def test_skyrim_streak_row_verbs():
    out = render_celebrate_for_theme(
        "skyrim",
        streak_rewards=_skyrim_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
        dual_blade_supported=True,
    )
    assert "oath kept" in out
    assert "curse fades" in out
    assert "↑2" in out
    assert "↓2" in out


def test_skyrim_levelup_uses_fleur_glyph_and_theme_name():
    """Levelup glyph is ⚜ (fleur-de-lis) — distinct from the meter ⚔.
    L8 in skyrim ladder is 'Pupil'."""
    out = render_celebrate_for_theme(
        "skyrim",
        streak_rewards=[],
        levelup={"to": "Pupil", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
        dual_blade_supported=True,
    )
    assert out is not None
    assert "⚜" in out
    assert "**Pupil** (L8) — next title at 125 XP" in out


def test_skyrim_fallback_does_not_swap_levelup_glyph():
    """⚜ (fleur-de-lis) is single-cell on every modern terminal — only
    ⚔ swaps to ✕. Levelup line uses ⚜ regardless of fallback state."""
    out = render_celebrate_for_theme(
        "skyrim",
        streak_rewards=[],
        levelup={"to": "Pupil", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
        dual_blade_supported=False,
    )
    assert out is not None
    assert "⚜" in out
    # Levelup-only branch emits no header glyph + no meter, so ⚔/✕ shouldn't
    # appear at all here.
    assert "⚔" not in out
    assert "✕" not in out


# -----------------------------------------------------------------------------
# Sort + truncate — pinned independently of any one theme. Group by direction
# (positive first, then negative), sort each group by streak desc.

def test_sort_and_truncate_order():
    """Positive group sorted by streak desc, then negative group sorted
    by streak desc. Determinism: name asc breaks streak ties."""
    rewards = [
        {"id": "a", "name": "a-late",   "streak": 1, "target": 5, "xp_awarded": 1, "direction": "negative"},
        {"id": "b", "name": "b-strong", "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "c", "name": "c-tied",   "streak": 2, "target": 5, "xp_awarded": 1, "direction": "positive"},
        {"id": "d", "name": "d-tied",   "streak": 2, "target": 5, "xp_awarded": 1, "direction": "positive"},
        {"id": "e", "name": "e-deep",   "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
    ]
    ordered, hidden = banner_themes._sort_and_truncate(rewards)
    assert hidden == 0
    assert [r["name"] for r in ordered] == [
        "b-strong",  # positive 4
        "c-tied",    # positive 2 (tied, alphabetical)
        "d-tied",    # positive 2
        "e-deep",    # negative 4
        "a-late",    # negative 1
    ]


def test_sort_and_truncate_caps_at_five():
    rewards = [
        {"id": str(i), "name": f"item-{i:02d}", "streak": (i % 5) + 1,
         "target": 5, "xp_awarded": 1, "direction": "positive"}
        for i in range(9)
    ]
    ordered, hidden = banner_themes._sort_and_truncate(rewards)
    assert len(ordered) == 5
    assert hidden == 4


def test_truncation_emits_more_tail_for_verb_style():
    """Forge with 9 rows → 5 shown + '…4 more' tail."""
    rewards = [
        {"id": str(i), "name": f"pattern-{i:02d}", "streak": 4,
         "target": 5, "xp_awarded": 2, "direction": "positive"}
        for i in range(9)
    ]
    out = render_celebrate_for_theme(
        "forge",
        streak_rewards=rewards,
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "…4 more" in out


# -----------------------------------------------------------------------------
# Hacker theme — divergent shape (no verb column, snake_case names, log
# frame). Pins the bespoke header, row format, and uplink/breach footer.

def _hacker_streak_fixture():
    return [
        {"id": "safe-git-hygiene", "name": "safe git hygiene",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "good-debugging", "name": "good debugging",
         "streak": 2, "target": 5, "xp_awarded": 1, "direction": "positive"},
        {"id": "heavy-subagent-delegation", "name": "heavy subagent delegation",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
    ]


def test_hacker_header_uses_shell_prompt_and_dashed_timestamp():
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=_hacker_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "> 👾 [coach@claw ~]$ tail -f session.log" in out
    assert "> ── 2026-05-06 19:00 → now" in out
    # Trailing dashes after the arrow.
    assert "→ now ────────────────" in out


def test_hacker_rows_use_snake_case_names_and_bracketed_xp():
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=_hacker_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    # snake_case: "safe git hygiene" → "safe_git_hygiene".
    assert "safe_git_hygiene" in out
    assert "good_debugging" in out
    assert "heavy_subagent_delegation" in out
    # XP format: [↑N xp] (lowercase, brackets, ↑ for both directions —
    # both kinds of pattern earn XP, the arrow denotes direction-of-XP-
    # movement, not direction-of-pattern).
    assert "[↑2 xp]" in out
    assert "[↑1 xp]" in out
    # Old broken format must NOT leak back.
    assert "[+" not in out
    # Direction is encoded by RUN/KILL row prefix.
    assert "RUN  safe_git_hygiene" in out
    assert "RUN  good_debugging" in out
    assert "KILL heavy_subagent_delegation" in out
    # Verb-style markers must NOT leak into hacker.
    assert "tempering" not in out
    assert "rising tide" not in out


def test_hacker_negative_direction_uses_kill_prefix():
    """Explicit negative-direction fixture (teammate-flagged P2). A
    weakness retiring renders with KILL prefix and [↑N xp] gain — the
    user earned XP for retiring the weakness, but the row name reads
    as the action they took rather than the bad behavior in isolation."""
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=[{
            "id": "heavy-subagent-delegation",
            "name": "heavy subagent delegation",
            "streak": 4, "target": 5, "xp_awarded": 2,
            "direction": "negative",
        }],
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "KILL heavy_subagent_delegation" in out
    assert "[↑2 xp]" in out
    # Old broken format must not regress.
    assert "[+2 xp]" not in out
    assert "[-2 xp]" not in out
    # Positive prefix must not leak onto a negative row.
    assert "RUN  heavy_subagent_delegation" not in out


def test_hacker_positive_direction_uses_run_prefix():
    """Symmetric pin: a strength reinforcing renders with RUN prefix."""
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=[{
            "id": "safe-git-hygiene",
            "name": "safe git hygiene",
            "streak": 4, "target": 5, "xp_awarded": 2,
            "direction": "positive",
        }],
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "RUN  safe_git_hygiene" in out
    assert "[↑2 xp]" in out
    # KILL prefix must not leak onto a positive row.
    assert "KILL safe_git_hygiene" not in out


def test_hacker_meter_uses_block_glyphs():
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=_hacker_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert "▓▓▓▓░" in out
    assert "▓▓░░░" in out
    # Other themes' meters must NOT appear.
    assert "≋" not in out
    assert "▰" not in out
    assert "⚔" not in out


def test_hacker_truncation_uses_ascii_dots_and_status_hint():
    rewards = [
        {"id": str(i), "name": f"pattern-{i:02d}", "streak": 4,
         "target": 5, "xp_awarded": 2, "direction": "positive"}
        for i in range(9)
    ]
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=rewards,
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    # Hacker uses ASCII `...` (3 dots), not `…` ellipsis.
    assert "...4 more" in out
    assert "(cat /coach/status)" in out
    # Ellipsis from verb-style themes must NOT appear.
    assert "…" not in out


def test_hacker_levelup_uses_uplink_and_breach_lines():
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=[],
        levelup={"to": "Hacker", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    # L8 in hacker theme ladder is "Hacker"; next threshold is 125.
    assert "> ::  📡 UPLINK ↑  L8 / Hacker 🥷 ::" in out
    assert "> next breach 🔓 125 xp" in out


def test_hacker_full_block_streak_plus_levelup():
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=_hacker_streak_fixture(),
        levelup={"to": "Hacker", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    header_pos = out.index("[coach@claw ~]$")
    row_pos = out.index("safe_git_hygiene")
    levelup_pos = out.index("UPLINK")
    breach_pos = out.index("next breach 🔓")
    assert header_pos < row_pos < levelup_pos < breach_pos


def test_hacker_slugs_do_not_leak_kebab_form():
    """The slug 'safe-git-hygiene' should not appear; only the snake_case
    transformed name 'safe_git_hygiene' should."""
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=_hacker_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert "safe-git-hygiene" not in out
    assert "heavy-subagent-delegation" not in out
    assert "safe_git_hygiene" in out
    assert "heavy_subagent_delegation" in out


# -----------------------------------------------------------------------------
# Military theme — divergent shape (tag-prefixed rows, rank ribbon footer).
# Pins the SITREP header, [PUSH]/[HOLD] tag rows, and the rank ribbon line
# that pulls medal_count + Roman numeral + ELO from stats.compute_for_render.

def _military_streak_fixture():
    return [
        {"id": "safe-git-hygiene", "name": "safe git hygiene",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "good-debugging", "name": "good debugging",
         "streak": 2, "target": 5, "xp_awarded": 1, "direction": "positive"},
        {"id": "heavy-subagent-delegation", "name": "heavy subagent delegation",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
        {"id": "commit-without-testing", "name": "commit without testing",
         "streak": 3, "target": 5, "xp_awarded": 1, "direction": "negative"},
    ]


def test_military_header_uses_sitrep_with_now_date_and_zulu_time():
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=_military_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    # SITREP uses *current* time (now), not oldest_entry_at.
    assert "> ◢  SITREP · 2026-05-07 · 1744Z" in out


def test_military_rows_use_push_hold_tags_and_xp_unit():
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=_military_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert "[PUSH] ▮▮▮▮▯  safe git hygiene" in out
    assert "[HOLD] ▮▮▮▮▯  heavy subagent delegation" in out
    # XP format includes 'XP' suffix for military (verb-style omits it).
    assert "↑2 XP" in out
    assert "↓2 XP" in out
    # Verb-style markers must NOT leak.
    assert "tempering" not in out
    assert "rising tide" not in out
    assert "oath kept" not in out


def test_military_rank_ribbon_at_l8():
    """At L8 lifetime=90: medal_count=2, roman=Ⅷ, elo=1257 (default
    1000-2800 range), name=Sensei, next_xp=125."""
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=[],
        levelup={"to": "Sensei", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    # Two medals at L8.
    assert "🎖️🎖️" in out
    # Roman numeral + ELO + bold name + next-promotion threshold.
    assert "Ⅷ" in out
    assert "**Sensei**" in out
    assert "promotion at 125 XP" in out
    # Lozenge sigil opens the rank line. 2-space indent matches the
    # locked footer cadence (compare to the verb-style footer indent).
    assert ">  ◆ 🎖️🎖️  Ⅷ  1257  **Sensei**" in out


def test_military_rank_ribbon_caps_at_5_medals_at_high_levels():
    """At L20+ medal_count is clamped to 5 — verifies the rank-ribbon
    scaling locked in compute_for_render."""
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=[],
        levelup={"to": "Paragon", "to_idx": 19, "xp_at_levelup": 840},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "🎖️🎖️🎖️🎖️🎖️" in out
    # No 6-medal regression.
    assert "🎖️🎖️🎖️🎖️🎖️🎖️" not in out


def test_military_streak_only_emits_no_rank_line():
    """No levelup → no rank ribbon. Just SITREP header + rows."""
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=_military_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    assert "🎖️" not in out
    assert "promotion at" not in out


def test_military_full_block_streak_plus_levelup_ordering():
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=_military_streak_fixture(),
        levelup={"to": "Sensei", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert out is not None
    sitrep_pos = out.index("SITREP")
    push_row_pos = out.index("[PUSH]")
    rank_line_pos = out.index("🎖️")
    assert sitrep_pos < push_row_pos < rank_line_pos


def test_military_uses_levelup_to_name_when_present():
    """If `levelup['to']` is present, prefer it over compute_for_render's
    LEVELS lookup. Lets a marker written under one theme still render its
    captured rank name even if the user has switched themes since."""
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=[],
        levelup={"to": "Sergeantmajor", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "**Sergeantmajor**" in out


# -----------------------------------------------------------------------------
# Catch-up framing — when caught_up=True, the disclaimer line should
# emit ONLY when no streak header is present (the streak header carries
# the date phrasing for streak banners; levelup-only / grad-only /
# reg-only bespoke banners have no header to do that work).

def test_caught_up_with_streak_does_not_emit_framing_line():
    """Streak banner has 'Tide turned · since X' — framing line stays
    suppressed. This is the locked v1 decision."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=_ocean_streak_fixture(),
        levelup=None,
        now=NOW,
        streak_oldest=YESTERDAY,
        caught_up=True,
    )
    assert out is not None
    assert "Milestones earned across earlier sessions" not in out


def test_caught_up_levelup_only_emits_framing_line():
    """Levelup-only bespoke banner has no theme header → framing line
    earns its keep, telling the user 'this isn't from the prompt you
    just typed'."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=[],
        levelup={"to": "Reefer", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
        caught_up=True,
    )
    assert out is not None
    assert "Milestones earned across earlier sessions" in out


def test_caught_up_grad_only_emits_framing_line():
    """Grad-only bespoke banner: same logic — no streak header, framing
    line should appear."""
    grads_block = "> 🎓⚡️ **GRADUATED: skipped search tools**  `+5 XP`"
    out = render_celebrate_for_theme(
        "skyrim",
        streak_rewards=[],
        levelup=None,
        grads_block=grads_block,
        now=NOW,
        streak_oldest=None,
        caught_up=True,
    )
    assert out is not None
    assert "Milestones earned across earlier sessions" in out


def test_caught_up_false_never_emits_framing_line():
    """caught_up=False → framing line never appears, regardless of
    section composition."""
    out = render_celebrate_for_theme(
        "ocean",
        streak_rewards=[],
        levelup={"to": "Reefer", "to_idx": 7, "xp_at_levelup": 90},
        now=NOW,
        streak_oldest=None,
        caught_up=False,
    )
    assert out is not None
    assert "Milestones earned across earlier sessions" not in out


# -----------------------------------------------------------------------------
# L50 max-rank handling — at the cap, "next at X XP" is wrong (compute_
# for_render returns None, _next_xp_after_levelup returns 0). Each theme
# swaps to a max-rank suffix that doesn't promise more progression.

@pytest.mark.parametrize("theme,expected_max_phrase,forbidden", [
    ("forge",  "the forge is mastered",      "next heat at"),
    ("ocean",  "all fathoms reached",        "next fathom at"),
    ("skyrim", "saga complete",              "next title at"),
])
def test_verb_style_l50_uses_max_template(theme, expected_max_phrase, forbidden):
    """At to_idx=49 (L50), the level-up footer swaps to the max template
    so it doesn't render 'next heat at 0 XP' / 'next fathom at 0 XP'."""
    out = render_celebrate_for_theme(
        theme,
        streak_rewards=[],
        levelup={"to": "Origin", "to_idx": 49, "xp_at_levelup": 5865},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert expected_max_phrase in out
    # No "next at 0 XP" — the bug being guarded against.
    assert forbidden not in out
    assert "0 XP" not in out


def test_military_l50_uses_highest_grade_suffix():
    """compute_for_render returns next_xp=None at L50 — military must
    NOT format that None into the string. Render 'highest grade' instead."""
    out = render_celebrate_for_theme(
        "military",
        streak_rewards=[],
        levelup={"to": "Polemarch", "to_idx": 49, "xp_at_levelup": 5865},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "highest grade" in out
    # The previously-broken paths.
    assert "promotion at None" not in out
    assert "promotion at 0" not in out


def test_hacker_l50_uses_root_access_line():
    """Hacker drops 'next breach 🔓 0 xp' for a max-rank line."""
    out = render_celebrate_for_theme(
        "hacker",
        streak_rewards=[],
        levelup={"to": "Singularity", "to_idx": 49, "xp_at_levelup": 5865},
        now=NOW,
        streak_oldest=None,
    )
    assert out is not None
    assert "root access 🔓 max layer reached" in out
    assert "next breach 🔓 0" not in out
