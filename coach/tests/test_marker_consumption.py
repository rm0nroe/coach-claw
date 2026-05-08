"""coach-user-prompt.py: per-session marker consumption.

Regression guard for BACKLOG P2 — pending marker files (`.pending_levelup`
etc.) used to be read-and-deleted by whichever UserPromptSubmit hook fired
first, so a target Claude Code session could lose its celebration banner
to an unrelated concurrent session. The fix tracks `consumed_by` inside
the marker JSON + a 24h TTL, so each session sees the marker once and
abandoned markers don't accumulate.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    """Load coach-user-prompt.py as a module."""
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_marker_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_marker(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_two_sessions_each_see_marker_once(cup, tmp_path):
    marker = tmp_path / ".pending_graduation"
    now = datetime.now(timezone.utc)
    _write_marker(marker, {
        "graduations": [{"id": "x", "name": "X"}],
        "created_at": now.isoformat(),
        "consumed_by": [],
    })

    a = cup._read_and_consume(marker, "session-A", now)
    assert a is not None
    assert a["graduations"] == [{"id": "x", "name": "X"}]

    b = cup._read_and_consume(marker, "session-B", now)
    assert b is not None
    assert b["graduations"] == [{"id": "x", "name": "X"}]

    # Marker still on disk (not deleted), with both sessions recorded.
    raw = json.loads(marker.read_text())
    assert sorted(raw["consumed_by"]) == ["session-A", "session-B"]


def test_same_session_polling_twice_renders_once(cup, tmp_path):
    marker = tmp_path / ".pending_levelup"
    now = datetime.now(timezone.utc)
    _write_marker(marker, {
        "from": "Drafter", "from_idx": 0,
        "to": "Builder", "to_idx": 1,
        "xp_at_levelup": 50,
        "created_at": now.isoformat(),
        "consumed_by": [],
    })

    first = cup._read_and_consume(marker, "session-A", now)
    assert first is not None
    assert first["to"] == "Builder"

    second = cup._read_and_consume(marker, "session-A", now)
    assert second is None, "same session should not re-render the same marker"


def test_marker_older_than_ttl_is_cleaned_up(cup, tmp_path):
    marker = tmp_path / ".pending_regression"
    long_ago = datetime.now(timezone.utc) - timedelta(hours=cup.MARKER_TTL_HOURS + 1)
    _write_marker(marker, {
        "regressions": [{"id": "y", "name": "Y"}],
        "created_at": long_ago.isoformat(),
        "consumed_by": [],
    })

    now = datetime.now(timezone.utc)
    result = cup._read_and_consume(marker, "session-A", now)
    assert result is None
    assert not marker.exists(), "expired marker should be unlinked on read"


def test_legacy_marker_without_created_at_is_treated_as_fresh(cup, tmp_path):
    """v0.1 markers were written without created_at / consumed_by fields.
    The reader must accept them on first encounter and stamp them."""
    marker = tmp_path / ".pending_streak_rewards"
    _write_marker(marker, {"rewards": [{"id": "z", "name": "Z", "streak": 2}]})

    now = datetime.now(timezone.utc)
    result = cup._read_and_consume(marker, "session-A", now)
    assert result is not None
    assert result["rewards"] == [{"id": "z", "name": "Z", "streak": 2}]

    # Stamped on first read.
    raw = json.loads(marker.read_text())
    assert "created_at" in raw
    assert raw["consumed_by"] == ["session-A"]


def test_corrupt_marker_json_is_swallowed(cup, tmp_path):
    marker = tmp_path / ".pending_graduation"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{not json")

    now = datetime.now(timezone.utc)
    result = cup._read_and_consume(marker, "session-A", now)
    assert result is None
    # Corrupt file is cleaned up to prevent a poll-loop.
    assert not marker.exists()


def test_consumed_by_overflow_drops_oldest(cup, tmp_path):
    marker = tmp_path / ".pending_levelup"
    now = datetime.now(timezone.utc)
    cap = cup.MARKER_CONSUMED_BY_CAP
    initial = [f"old-session-{i}" for i in range(cap)]
    _write_marker(marker, {
        "from": "X", "from_idx": 0, "to": "Y", "to_idx": 1, "xp_at_levelup": 1,
        "created_at": now.isoformat(),
        "consumed_by": list(initial),
    })

    result = cup._read_and_consume(marker, "new-session", now)
    assert result is not None
    raw = json.loads(marker.read_text())
    assert len(raw["consumed_by"]) == cap
    assert "new-session" in raw["consumed_by"]
    assert raw["consumed_by"][0] != "old-session-0"  # oldest dropped


def test_missing_session_key_uses_fallback(cup, tmp_path):
    """If both transcript_path and session_id are absent, the hook passes
    None for session_key. The reader uses 'unknown' so the marker still
    deduplicates instead of re-firing on every prompt."""
    marker = tmp_path / ".pending_graduation"
    now = datetime.now(timezone.utc)
    _write_marker(marker, {
        "graduations": [{"id": "g", "name": "G"}],
        "created_at": now.isoformat(),
        "consumed_by": [],
    })

    first = cup._read_and_consume(marker, None, now)
    assert first is not None

    second = cup._read_and_consume(marker, None, now)
    assert second is None, "successive None-keyed polls should still dedupe"

    raw = json.loads(marker.read_text())
    assert raw["consumed_by"] == ["unknown"]


def test_missing_marker_returns_none(cup, tmp_path):
    marker = tmp_path / ".pending_graduation"  # never created
    now = datetime.now(timezone.utc)
    assert cup._read_and_consume(marker, "session-A", now) is None
