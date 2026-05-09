"""coach-user-prompt.py: wrap-announce + wrap-duplicate banner gating.

The plugin auto-wraps a claimed statusLine on first encounter; the
`.statusline-wrap-announced` marker tells the next user-prompt hook to
surface a one-time banner explaining what happened. Symmetric:
`.statusline-wrap-duplicate-detected` is dropped by the runtime
composer when it sees a Coach signature already in the original
output, and the hook surfaces a "consider unwrapping" banner.

Both markers use the consumed-by pattern (per-session dedup, 24h TTL),
mirroring LEVELUP_MARKER et al.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    repo_path = (
        Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    )
    path = (
        repo_path
        if repo_path.exists()
        else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    )
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_wrap_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(tmp_path, monkeypatch, cup):
    """Redirect marker paths to a tmpdir so tests don't touch real
    `~/.claude/coach/` state."""
    monkeypatch.setattr(cup, "COACH_DIR", tmp_path)
    monkeypatch.setattr(
        cup, "WRAP_ANNOUNCE_MARKER", tmp_path / ".statusline-wrap-announced"
    )
    monkeypatch.setattr(
        cup, "WRAP_DUPLICATE_MARKER", tmp_path / ".statusline-wrap-duplicate-detected"
    )
    return tmp_path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_announce_block_silent_when_no_marker(cup, isolated):
    """No marker → no banner."""
    out = cup._maybe_wrap_announce_block("session-A", _now())
    assert out is None


def test_announce_block_emits_once_per_session(cup, isolated):
    """Marker present → first call emits, second call (same session)
    is silent (consumed-by dedup)."""
    isolated.joinpath(".statusline-wrap-announced").write_text(json.dumps({
        "created_at": _now().isoformat(),
        "consumed_by": [],
    }))
    first = cup._maybe_wrap_announce_block("session-A", _now())
    second = cup._maybe_wrap_announce_block("session-A", _now())
    assert first is not None
    assert "wrapped" in first.lower()
    assert "/coach-claw:doctor --unwrap-statusline" in first
    assert second is None


def test_announce_block_separate_sessions_each_see_marker_once(cup, isolated):
    """Two different sessions each consume the marker once."""
    isolated.joinpath(".statusline-wrap-announced").write_text(json.dumps({
        "created_at": _now().isoformat(),
        "consumed_by": [],
    }))
    a = cup._maybe_wrap_announce_block("session-A", _now())
    b = cup._maybe_wrap_announce_block("session-B", _now())
    assert a is not None
    assert b is not None


def test_announce_block_renders_terminal_blockquote(cup, isolated):
    isolated.joinpath(".statusline-wrap-announced").write_text(json.dumps({
        "created_at": _now().isoformat(),
        "consumed_by": [],
    }))
    out = cup._maybe_wrap_announce_block("session-A", _now(), env="terminal")
    assert out.startswith(">")


def test_announce_block_renders_ide_hr_frame(cup, isolated):
    isolated.joinpath(".statusline-wrap-announced").write_text(json.dumps({
        "created_at": _now().isoformat(),
        "consumed_by": [],
    }))
    out = cup._maybe_wrap_announce_block("session-A", _now(), env="ide")
    assert out.startswith("---")


def test_duplicate_block_silent_when_no_marker(cup, isolated):
    out = cup._maybe_wrap_duplicate_block("session-A", _now())
    assert out is None


def test_duplicate_block_emits_when_marker_present(cup, isolated):
    isolated.joinpath(".statusline-wrap-duplicate-detected").write_text(json.dumps({
        "created_at": _now().isoformat(),
        "consumed_by": [],
    }))
    out = cup._maybe_wrap_duplicate_block("session-A", _now())
    assert out is not None
    assert "duplicate" in out.lower()
    assert "/coach-claw:doctor --unwrap-statusline" in out


def test_failsafe_returns_none_on_corrupt_marker(cup, isolated):
    """Corrupt JSON in the marker → never raise; return None."""
    isolated.joinpath(".statusline-wrap-announced").write_text("{not json")
    out = cup._maybe_wrap_announce_block("session-A", _now())
    assert out is None
