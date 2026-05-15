"""Anti-leak gates for the v1.0.10 positive-inverse contract.

The earning surfaces (mid-streak banners, negative graduations,
weakness tip-completion acks) show the POSITIVE INVERSE name. The
slipping/operator/model surfaces (regression banner, /coach status,
SessionStart <coach> watchlist, tip-generator prompt) show the
CANONICAL name. This file pins the boundary in both directions.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "coach" / "bin"))
sys.path.insert(0, str(REPO / "hooks"))


@pytest.fixture(scope="module")
def session_start():
    """Load coach-session-start.py as a module (its filename has a
    dash so a normal import doesn't work). Cached at module scope."""
    spec = importlib.util.spec_from_file_location(
        "coach_session_start_under_test",
        str(REPO / "hooks" / "coach-session-start.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_profile_with_weakness():
    return {
        "schema_version": 1,
        "entries": [
            {
                "id": "commit-without-testing",
                "name": "committing without testing",
                "direction": "negative",
                "tier": "active",
                "clean_streak_runs": 2,
                "nudge": "Run a test command before each commit.",
                "examples": ["edited 12 files, no pytest"],
            },
        ],
        "recent_runs": [],
        "graduated": [],
        "archived": [],
        "strengths": [],
    }


def test_session_start_watchlist_uses_canonical_name(
    session_start, fake_profile_with_weakness
):
    """The SessionStart <coach> block is LLM diagnostic context — the
    model reads it to translate into a user-facing tip. It must show
    the canonical negative name; if the positive inverse leaks here,
    the model receives an incoherent signal ("the user has a weakness
    called testing before committing")."""
    out = session_start._format_context(
        entries=fake_profile_with_weakness["entries"],
        changelog_delta=None,
        skill_hints=[],
        env="terminal",
    )
    assert "committing without testing" in out, (
        "SessionStart watchlist must show the canonical negative name "
        "for LLM diagnostic context"
    )
    assert "testing before committing" not in out, (
        "Positive inverse leaked into LLM diagnostic context — would "
        "confuse tip generation"
    )


def test_tip_generator_prompt_uses_canonical_name():
    """The UserPromptSubmit hook's <coach-tip> block hands the tip's
    diagnostic context to the model under the literal field name
    `PATTERN / SKILL:`. This must be canonical; the model uses it to
    pick the right framing for the user-facing tip body itself."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "cup_for_anti_leak",
        str(REPO / "hooks" / "coach-user-prompt.py"),
    )
    cup = _ilu.module_from_spec(spec)
    spec.loader.exec_module(cup)
    # Build a tip dict matching the structure cup._render_tip_instructions
    # consumes. Direct render call would require full hook plumbing; the
    # simpler structural check is that nothing in the render path
    # rewrites tip["name"] via positive_frame.
    src = (REPO / "hooks" / "coach-user-prompt.py").read_text()
    # The literal "PATTERN / SKILL:" line must reference tip['name'],
    # NOT a positive-inverse resolve. Direct grep is the right gate.
    assert "PATTERN / SKILL: {tip['name']}" in src, (
        "Tip generator prompt line shape changed — re-verify framing"
    )
    # And no positive_frame=True is anywhere near the tip-rendering
    # block (the simple anti-leak: positive_frame only appears in
    # earning-surface render functions, not in tip-prompt builders).
    snippet = src.split("TIP KIND:")[1][:1500]
    assert "positive_frame" not in snippet, (
        "positive_frame=True appears in the tip-generator-prompt window — "
        "would break LLM diagnostic context"
    )


def test_status_module_uses_canonical_display_name():
    """coach/bin/status.py is operator view — weakness section header
    is "Weaknesses" and the row text MUST match (canonical name).
    Structural check: the two display_name() calls in status.py must
    NOT pass positive_frame=True."""
    src = (REPO / "coach" / "bin" / "status.py").read_text()
    assert "display_name(eid, profile, positive_frame" not in src, (
        "status.py is calling display_name with positive_frame — "
        "operator view should always be canonical"
    )
    # Belt-and-braces: the canonical call shape is what we expect
    assert "display_name(eid, profile)" in src
