"""coach-user-prompt.py: bespoke-theme dispatch in _assemble_celebrate_block.

Pins the wiring between the hook's celebrate-block assembler and the
banner_themes module. Specifically:

  * craft theme + terminal: produces the historical default shape, byte-
    identical to pre-feature output. No regression for the seven default
    themes.
  * bespoke theme + terminal: triggers banner_themes rendering.
  * bespoke theme + ide: bypasses banner_themes (bespoke is terminal-only).
  * banner_themes raises: hook falls through to default rendering — the
    "hook crash never breaks a session" invariant.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

import stats


@pytest.fixture(autouse=True)
def _hermetic_stats_globals(monkeypatch):
    monkeypatch.setattr(stats, "LEVELS", stats._build_level_ladder())
    monkeypatch.setattr(stats, "ELO_MIN", 1000)
    monkeypatch.setattr(stats, "ELO_MAX", 2800)


@pytest.fixture(scope="module")
def cup():
    """Load hooks/coach-user-prompt.py with bundle modules importable.

    The hook's first action is `sys.path.insert(0, COACH_DIR/bin)` where
    COACH_DIR points at the LIVE install (~/.claude/coach). That live
    install may be stale relative to this checkout, so tests must not
    trust whatever banner_themes happens to import during hook load.

    We work around that here by:
      1. Evicting any cached versions of the modules the hook will load.
      2. Making bundle bin importable before executing the hook.
      3. After exec, always evicting/re-importing banner_themes from the
         bundle and patching the cup module so assertions pin source-tree
         behavior, not installed-state behavior.

    Production hook behavior is unchanged — this is purely for dev-time
    test hermeticity."""
    import sys
    bundle_bin = str(Path(__file__).resolve().parents[2] / "coach" / "bin")

    # Evict cached versions before loading.
    for name in ("render_env", "banner_themes", "stats",
                 "user_config", "themes"):
        sys.modules.pop(name, None)

    # Add bundle bin to sys.path (front). The hook's own insert at line
    # 48 will bump this to position 1, but Python's import walks the
    # whole list — bundle's banner_themes will be found there.
    if bundle_bin not in sys.path:
        sys.path.insert(0, bundle_bin)

    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_bespoke_dispatch", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ALWAYS force-load banner_themes from the bundle (not the live
    # install). The hook's own `sys.path.insert(0, COACH_DIR/bin)` at
    # line 48 puts the live install ahead of bundle, so without this
    # evict-and-reimport step the test pins assertions against whatever
    # banner_themes happens to be installed at ~/.claude/coach/bin/. We
    # want tests to verify the BUNDLE's behavior — that's source of truth.
    for name in ("render_env", "banner_themes"):
        sys.modules.pop(name, None)
    # Bundle bin must be ahead of live install for the re-import.
    sys.path.insert(0, bundle_bin)
    try:
        from banner_themes import (  # noqa: E402
            render_celebrate_for_theme,
            BESPOKE_THEMES,
        )
        mod._render_celebrate_for_theme = render_celebrate_for_theme
        mod._BESPOKE_THEMES = BESPOKE_THEMES
        mod._BESPOKE_OK = True
    except Exception as e:
        pytest.skip(f"could not load banner_themes from bundle: {e}")
    return mod


NOW = datetime(2026, 5, 7, 17, 44, tzinfo=timezone.utc)
YESTERDAY = datetime(2026, 5, 6, 19, 0, tzinfo=timezone.utc)


def _streak_fixture():
    return [
        {"id": "safe-git-hygiene", "name": "safe git hygiene",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "positive"},
        {"id": "heavy-subagent-delegation", "name": "heavy subagent delegation",
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
    ]


# -----------------------------------------------------------------------------
# Default themes — craft / cosmic / marvel / dc / finalfantasy / lotr /
# starwars MUST render the historical default shape. No bespoke dispatch.

def test_craft_theme_terminal_renders_default_shape(cup):
    """craft + terminal must produce the default `> ↑ name 🟢🟢🟢🟢⚪ ...`
    shape — byte-for-byte regression guard for the seven untouched themes."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup=None,
        caught_up=False,
        env="terminal",
        theme="craft",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert block is not None
    # Default streak rendering uses 🟢⚪ meter and inline backtick spans.
    # v1.0.10 earning-surface contract: arrow is always ↑ (XP credit
    # direction); negative-direction rows render the positive inverse
    # name (heavy-subagent-delegation → right-sized delegation).
    assert "> ↑ `+2` · `safe git hygiene` `🟢🟢🟢🟢⚪` 4/5" in block
    assert "> ↑ `+2` · `right-sized delegation` `🟢🟢🟢🟢⚪` 4/5" in block
    # Canonical negative name must NOT leak on the earning surface.
    assert "heavy subagent delegation" not in block
    # Bespoke header / glyphs MUST NOT appear.
    assert "Tide turned" not in block
    assert "🦞" not in block
    assert "≋" not in block


def test_default_theme_with_caught_up_keeps_framing_line(cup):
    """The catch-up framing line is part of the default shape and stays
    in place for default themes — only bespoke themes drop it."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup=None,
        caught_up=True,
        env="terminal",
        theme="cosmic",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert block is not None
    assert "Milestones earned across earlier sessions" in block


# -----------------------------------------------------------------------------
# Bespoke themes — terminal triggers banner_themes; IDE keeps default.

def test_ocean_theme_terminal_uses_bespoke_render(cup):
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup=None,
        caught_up=True,
        env="terminal",
        theme="ocean",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert block is not None
    assert "🦞  Tide turned · since yesterday" in block
    assert "≋≋≋≋·  safe git hygiene" in block
    # Catch-up framing line is REMOVED for bespoke themes — header carries
    # the date instead.
    assert "Milestones earned across earlier sessions" not in block


def test_bespoke_theme_normalizes_streak_reward_names(cup):
    """Regression: bespoke themes (ocean/forge/skyrim/military/hacker)
    used to render `r["name"]` raw, leaking slugs whenever the marker
    payload had `name == id` (the analyze.py:295 bug class). The Pass C
    normalization in _assemble_celebrate_block now resolves names via
    display_name BEFORE the bespoke dispatch, so all five bespoke themes
    inherit the same humanized rendering as the default-theme path."""
    bad_marker = [
        {"id": "under-planning", "name": "under-planning",  # slug-as-name
         "streak": 4, "target": 5, "xp_awarded": 2, "direction": "negative"},
    ]
    # hacker theme snake-cases names by design ("thin planning" →
    # "thin_planning"); other bespoke themes keep the space form.
    expected_form = {
        "ocean":    "thin planning",
        "forge":    "thin planning",
        "skyrim":   "thin planning",
        "military": "thin planning",
        "hacker":   "thin_planning",
    }
    for theme, expected in expected_form.items():
        block = cup._assemble_celebrate_block(
            grads=[],
            regs=[],
            streak_rewards=bad_marker,
            levelup=None,
            caught_up=False,
            env="terminal",
            theme=theme,
            now=NOW,
            streak_oldest=YESTERDAY,
        )
        assert block is not None, f"{theme} produced no block"
        assert "under-planning" not in block, (
            f"{theme} leaked the kebab slug — Pass C normalization regressed"
        )
        assert "under_planning" not in block, (
            f"{theme} leaked the snake-cased slug — name field wasn't normalized"
        )
        assert expected in block, (
            f"{theme} did not render the curated override (expected {expected!r})"
        )


def test_ocean_theme_ide_falls_back_to_default(cup):
    """IDE rendering is terminal-only for bespoke themes. ocean + ide must
    produce the default HR-framed shape, not bespoke ASCII frames."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup=None,
        caught_up=False,
        env="ide",
        theme="ocean",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert block is not None
    # IDE shape uses HR frames; bespoke ocean header MUST NOT appear.
    assert "Tide turned" not in block
    assert "🦞  Tide turned" not in block
    assert "≋≋≋≋·" not in block


def test_hacker_theme_terminal_uses_bespoke_render(cup):
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup={"to": "Hacker", "to_idx": 7, "xp_at_levelup": 90},
        caught_up=False,
        env="terminal",
        theme="hacker",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert block is not None
    assert "[coach@claw ~]$ tail -f session.log" in block
    assert "safe_git_hygiene" in block
    # Direction prefix on each row — RUN for positive, KILL for negative.
    assert "RUN  safe_git_hygiene" in block
    assert "KILL heavy_subagent_delegation" in block
    # XP column uses [↑N xp] for both directions (gain in either case).
    assert "[↑2 xp]" in block
    assert "UPLINK ↑  L8 / Hacker 🥷" in block
    assert "next breach 🔓 125 xp" in block


def test_military_theme_terminal_uses_bespoke_render(cup):
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup={"to": "Sensei", "to_idx": 7, "xp_at_levelup": 90},
        caught_up=False,
        env="terminal",
        theme="military",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    assert block is not None
    assert "SITREP" in block
    assert "[PUSH] ▮▮▮▮▯  safe git hygiene" in block
    assert "🎖️🎖️" in block  # 2 medals at L8
    assert "Ⅷ" in block
    assert "**Sensei**" in block


# -----------------------------------------------------------------------------
# Failure path — if banner_themes raises, the hook must fall back to
# default rendering. Pins the "hook crash never breaks a session" invariant.

def test_bespoke_render_failure_falls_back_to_default(cup, monkeypatch):
    """Inject an exception into banner_themes.render_celebrate_for_theme
    and verify the hook still produces a default-shape banner."""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated bespoke crash")

    monkeypatch.setattr(cup, "_render_celebrate_for_theme", boom)
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=_streak_fixture(),
        levelup=None,
        caught_up=False,
        env="terminal",
        theme="ocean",
        now=NOW,
        streak_oldest=YESTERDAY,
    )
    # Must produce a non-None banner (default shape) despite the crash.
    assert block is not None
    # Default shape markers — same as the craft regression test above.
    assert "> ↑ `+2` · `safe git hygiene`" in block
    # No bespoke leakage.
    assert "🦞" not in block
    assert "≋" not in block


def test_bespoke_dispatch_returns_none_when_nothing_to_render(cup):
    """No streak rewards, no levelup, no grads/regs → None.
    Mirrors the default path's empty-input behavior."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=[],
        levelup=None,
        caught_up=False,
        env="terminal",
        theme="ocean",
        now=NOW,
        streak_oldest=None,
    )
    assert block is None
