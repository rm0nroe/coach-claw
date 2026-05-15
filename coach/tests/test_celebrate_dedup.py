"""coach-user-prompt.py: _assemble_celebrate_block deduplicates queued
markers and surfaces a catch-up framing line when banners predate today.

Pinned by a real bug: queued /coach-insights events accumulate in the
.pending_streak_rewards / .pending_graduation marker files. Without
dedup, two ticks for the same pattern (2/5 + 3/5) both rendered, and a
graduation didn't suppress its same-batch tick. Without catch-up
framing, queued events looked like they came from the user's first
command in the session.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_under_test_celebrate", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def now():
    return datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# Per-pattern dedup: highest streak wins
# -----------------------------------------------------------------------------

def test_dedup_keeps_highest_streak_per_pattern(cup, now):
    """Two ticks for the same pattern (e.g. yesterday's 2/5 + today's 3/5
    accumulated unconsumed) collapse to ONE banner showing the higher
    streak — the lower one is subsumed."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=[
            {"id": "effective-skill-use", "name": "effective skill use",
             "direction": "positive", "streak": 2, "target": 5, "xp_awarded": 1},
            {"id": "effective-skill-use", "name": "effective skill use",
             "direction": "positive", "streak": 3, "target": 5, "xp_awarded": 1},
        ],
        levelup=None,
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    # Exactly ONE positive-direction streak banner, with the 3/5 streak.
    assert block.count("> ↑ ") == 1
    assert "3/5" in block
    assert "2/5" not in block


def test_dedup_handles_missing_id_gracefully(cup, now):
    """Marker entries without an id are dropped (defensive: malformed
    legacy markers shouldn't crash the pipeline)."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=[
            {"id": "", "name": "broken", "direction": "negative", "streak": 1,
             "target": 5, "xp_awarded": 1},
            {"id": "valid-pattern", "name": "valid pattern",
             "direction": "negative", "streak": 2, "target": 5, "xp_awarded": 1},
        ],
        levelup=None,
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    # Only the valid one renders. Earning-surface contract (v1.0.10):
    # row leads with ↑ regardless of direction (arrow tracks XP credit).
    assert block.count("> ↑ ") == 1
    assert "valid pattern" in block


# -----------------------------------------------------------------------------
# Graduation suppresses same-batch tick
# -----------------------------------------------------------------------------

def test_graduation_suppresses_same_batch_tick(cup, now):
    """If a pattern graduated and also has a queued tick for the same
    batch, the graduation banner renders alone — no redundant tick."""
    block = cup._assemble_celebrate_block(
        grads=[
            {"id": "safe-git-hygiene", "name": "safe git hygiene",
             "direction": "positive", "graduated_reason": "present-5-runs"},
        ],
        regs=[],
        streak_rewards=[
            # The 4/5 tick that was queued from the prior insights run.
            {"id": "safe-git-hygiene", "name": "safe git hygiene",
             "direction": "positive", "streak": 4, "target": 5, "xp_awarded": 2},
        ],
        levelup=None,
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    # Mastery banner present; tick banner absent.
    assert "🎓🌟 **MASTERED: safe git hygiene**" in block
    assert "> ↑ " not in block
    assert "4/5" not in block


def test_graduation_doesnt_suppress_other_patterns_ticks(cup, now):
    """Suppression is per-id — graduations don't kill ticks for
    unrelated patterns in the same batch."""
    block = cup._assemble_celebrate_block(
        grads=[
            {"id": "safe-git-hygiene", "name": "safe git hygiene",
             "direction": "positive", "graduated_reason": "present-5-runs"},
        ],
        regs=[],
        streak_rewards=[
            {"id": "safe-git-hygiene", "name": "safe git hygiene",
             "direction": "positive", "streak": 4, "target": 5, "xp_awarded": 2},
            {"id": "effective-skill-use", "name": "effective skill use",
             "direction": "positive", "streak": 3, "target": 5, "xp_awarded": 1},
        ],
        levelup=None,
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    assert "🎓🌟 **MASTERED: safe git hygiene**" in block
    # safe-git-hygiene tick gone, effective-skill-use tick survives.
    assert block.count("> ↑ ") == 1
    assert "effective skill use" in block


# -----------------------------------------------------------------------------
# Catch-up framing
# -----------------------------------------------------------------------------

CATCHUP_LINE = "Milestones earned across earlier sessions"


def test_catchup_prefix_when_caught_up_true(cup, now):
    """When caught_up=True, the catch-up framing line appears between
    the verbatim-render instruction and the banners."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=[
            {"id": "p1", "name": "pattern one", "direction": "negative",
             "streak": 1, "target": 5, "xp_awarded": 1},
        ],
        levelup=None,
        caught_up=True,
        env="terminal",
    )
    assert block is not None
    assert CATCHUP_LINE in block


def test_catchup_prefix_absent_when_caught_up_false(cup, now):
    """When caught_up=False, no catch-up line — banners look like fresh
    same-session events."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=[
            {"id": "p1", "name": "pattern one", "direction": "negative",
             "streak": 1, "target": 5, "xp_awarded": 1},
        ],
        levelup=None,
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    assert CATCHUP_LINE not in block


# -----------------------------------------------------------------------------
# _marker_predates_today: drives `caught_up`
# -----------------------------------------------------------------------------

def test_marker_predates_today_returns_true_for_yesterday(cup, now):
    payload = {"created_at": (now - timedelta(days=2)).isoformat()}
    assert cup._marker_predates_today(payload, now) is True


def test_marker_predates_today_returns_false_for_same_day(cup, now):
    payload = {"created_at": now.isoformat()}
    assert cup._marker_predates_today(payload, now) is False


def test_marker_predates_today_handles_missing_data(cup, now):
    # No payload, empty payload, no created_at — all safe.
    assert cup._marker_predates_today(None, now) is False
    assert cup._marker_predates_today({}, now) is False
    assert cup._marker_predates_today({"created_at": None}, now) is False
    assert cup._marker_predates_today({"created_at": "garbage"}, now) is False


def test_marker_predates_today_prefers_oldest_entry_at(cup, now):
    """When both fields are present, oldest_entry_at wins. This is the
    real-world post-fix shape: today's append updates created_at to now
    but oldest_entry_at still anchors at the prior write.

    Pre-v0.4.2 this test would have failed (catch-up went silent for
    carried-over entries because only created_at was inspected)."""
    payload = {
        "created_at": now.isoformat(),                          # today's append
        "oldest_entry_at": (now - timedelta(days=1)).isoformat(),  # prior write
    }
    assert cup._marker_predates_today(payload, now) is True


def test_marker_predates_today_falls_back_to_created_at_for_legacy(cup, now):
    """Markers written before v0.4.2 don't have oldest_entry_at. The
    catch-up predicate must still work for them via created_at fallback."""
    payload = {"created_at": (now - timedelta(days=2)).isoformat()}
    # No oldest_entry_at field — should still detect the predates-today case.
    assert cup._marker_predates_today(payload, now) is True


# -----------------------------------------------------------------------------
# atomic_marker_rmw_append: oldest_entry_at preservation across appends
# -----------------------------------------------------------------------------

@pytest.fixture
def marker_io_mod():
    """Load coach/bin/marker_io.py for direct producer-side testing."""
    path = Path(__file__).resolve().parents[1] / "bin" / "marker_io.py"
    if not path.exists():
        pytest.skip(f"marker_io.py not found at {path}")
    spec = importlib.util.spec_from_file_location("marker_io_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_carried_over_append_preserves_oldest_entry_at(cup, marker_io_mod, tmp_path, now):
    """The teammate-reported P2 bug, end-to-end:

    Yesterday's /coach-insights writes streak markers; user doesn't
    consume them; today's /coach-insights appends fresh markers via
    atomic_marker_rmw_append. Without the fix, the marker's top-level
    created_at gets reset to today, and _marker_predates_today returns
    False — silently dropping catch-up framing for the carried-over
    entries from yesterday.

    With the fix, oldest_entry_at preserves yesterday's timestamp
    across the append, so catch-up correctly fires."""
    import json
    yesterday = now - timedelta(days=1)
    path = tmp_path / ".pending_streak_rewards"

    # Day N-1: yesterday's insights run leaves a marker with one entry.
    marker_io_mod.atomic_marker_rmw_append(
        path, "rewards",
        [{"id": "old-pattern", "name": "old pattern", "direction": "negative",
          "streak": 1, "target": 5, "xp_awarded": 1}],
        yesterday,
    )

    # Day N: today's insights run appends a fresh entry to the same marker.
    marker_io_mod.atomic_marker_rmw_append(
        path, "rewards",
        [{"id": "new-pattern", "name": "new pattern", "direction": "negative",
          "streak": 2, "target": 5, "xp_awarded": 1}],
        now,
    )

    payload = json.loads(path.read_text())

    # Both entries present.
    assert [r["id"] for r in payload["rewards"]] == ["old-pattern", "new-pattern"]

    # Top-level created_at advanced to today's write (drives TTL).
    assert payload["created_at"] == now.isoformat()

    # oldest_entry_at preserved from yesterday's write (drives catch-up).
    assert payload["oldest_entry_at"] == yesterday.isoformat()

    # And the predicate the consumer uses returns True.
    assert cup._marker_predates_today(payload, now) is True


def test_first_append_anchors_oldest_entry_at_at_now(marker_io_mod, tmp_path, now):
    """A fresh marker (no prior file) anchors oldest_entry_at at `now`,
    so a same-day append doesn't spuriously trigger catch-up framing."""
    import json
    path = tmp_path / ".pending_streak_rewards"

    marker_io_mod.atomic_marker_rmw_append(
        path, "rewards",
        [{"id": "p1", "name": "pattern one", "direction": "negative",
          "streak": 1, "target": 5, "xp_awarded": 1}],
        now,
    )

    payload = json.loads(path.read_text())
    assert payload["created_at"] == now.isoformat()
    assert payload["oldest_entry_at"] == now.isoformat()


def test_append_against_legacy_marker_promotes_created_at(marker_io_mod, tmp_path, now):
    """An existing marker written by pre-v0.4.2 marker_io has no
    oldest_entry_at field. The first append after upgrade must promote
    the existing created_at into oldest_entry_at so users on the
    upgrade boundary still get catch-up framing for entries they
    haven't consumed yet."""
    import json
    path = tmp_path / ".pending_streak_rewards"
    yesterday = now - timedelta(days=1)

    # Hand-write a legacy-shape marker (no oldest_entry_at).
    legacy = {
        "rewards": [{"id": "legacy", "name": "legacy", "direction": "negative",
                     "streak": 1, "target": 5, "xp_awarded": 1}],
        "created_at": yesterday.isoformat(),
        "consumed_by": [],
    }
    path.write_text(json.dumps(legacy))

    # Today's append should promote yesterday's created_at into oldest_entry_at.
    marker_io_mod.atomic_marker_rmw_append(
        path, "rewards",
        [{"id": "fresh", "name": "fresh", "direction": "negative",
          "streak": 2, "target": 5, "xp_awarded": 1}],
        now,
    )

    payload = json.loads(path.read_text())
    assert payload["created_at"] == now.isoformat()
    assert payload["oldest_entry_at"] == yesterday.isoformat()
    assert [r["id"] for r in payload["rewards"]] == ["legacy", "fresh"]


# -----------------------------------------------------------------------------
# Empty case: returns None, not an empty <coach-celebrate> block
# -----------------------------------------------------------------------------

def test_assemble_returns_none_when_no_events(cup, now):
    """No queued events → return None so the consumer skips emitting
    a celebrate block entirely."""
    assert cup._assemble_celebrate_block(
        grads=[], regs=[], streak_rewards=[], levelup=None,
        caught_up=False, env="terminal",
    ) is None


# -----------------------------------------------------------------------------
# Verbatim-render contract: instruction header + closing tag
# -----------------------------------------------------------------------------

def test_celebrate_block_includes_verbatim_instruction(cup, now):
    """The render-verbatim instruction must be present so Claude knows
    not to re-interpret labels or substitute slugs for names."""
    block = cup._assemble_celebrate_block(
        grads=[],
        regs=[],
        streak_rewards=[{"id": "p1", "name": "pattern one",
                         "direction": "negative", "streak": 1, "target": 5,
                         "xp_awarded": 1}],
        levelup=None,
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    assert block.startswith("<coach-celebrate>\n")
    assert block.endswith("\n</coach-celebrate>")
    assert "Render this block VERBATIM" in block
    # The old "pick by direction" / "Rules for every banner" instruction
    # footer must NOT come back — its presence was the bug surface.
    assert "pick by direction" not in block
    assert "Rules for every banner" not in block


def test_celebrate_combines_all_event_kinds(cup, now):
    """Regression + streak + graduation + level-up — verify ordering and
    that all four sections are present without bleeding into each other."""
    # Use slug-form ids whose humanized fallback (`-` → space) matches
    # the asserted display text. display_name() is now the single
    # source of truth — marker `name` is ignored, so synthetic ids
    # without an override render via humanized-slug fallback.
    block = cup._assemble_celebrate_block(
        grads=[{"id": "pattern-g", "name": "pattern g", "direction": "positive",
                "graduated_reason": "present-5-runs"}],
        regs=[{"id": "pattern-r", "name": "pattern r",
               "originally_graduated_at": "2026-04-01"}],
        streak_rewards=[{"id": "pattern-s", "name": "pattern s",
                         "direction": "negative", "streak": 2, "target": 5,
                         "xp_awarded": 1}],
        levelup={"from": "L3 X", "to": "Y", "to_idx": 3, "xp_at_levelup": 100},
        caught_up=False,
        env="terminal",
    )
    assert block is not None
    # All four banner heads present. v1.0.10 contract: regressions use
    # "Bad habit returned:" (slipping surface, canonical name);
    # streaks use ↑ (earning surface); graduations use MASTERED regardless
    # of direction (the glyph distinguishes origin).
    assert "**Bad habit returned: pattern r**" in block
    assert "> ↑ " in block
    assert "**MASTERED: pattern g**" in block
    assert "**Level up!**" in block
    # Documented order: regressions, streaks, graduations, level-up.
    pos_reg = block.index("Bad habit returned:")
    pos_streak = block.index("> ↑ ")
    pos_grad = block.index("MASTERED:")
    pos_levelup = block.index("Level up!")
    assert pos_reg < pos_streak < pos_grad < pos_levelup
