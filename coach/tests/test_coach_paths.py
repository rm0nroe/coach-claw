"""coach_paths.py — single source of truth for ~/.claude/coach/ resolution."""
from __future__ import annotations

from pathlib import Path

import coach_paths


def test_resolve_default_path(monkeypatch):
    """No COACH_CONFIG_DIR set → falls back to ~/.claude/coach."""
    monkeypatch.delenv("COACH_CONFIG_DIR", raising=False)
    assert coach_paths.resolve_coach_dir() == Path.home() / ".claude" / "coach"


def test_resolve_honors_env_override(tmp_path, monkeypatch):
    """COACH_CONFIG_DIR overrides the default path."""
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path))
    assert coach_paths.resolve_coach_dir() == tmp_path


def test_resolve_per_call(tmp_path, monkeypatch):
    """Resolution happens at every call, not cached at import time —
    tests/wrappers can flip the env var mid-process."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    monkeypatch.setenv("COACH_CONFIG_DIR", str(a))
    assert coach_paths.resolve_coach_dir() == a

    monkeypatch.setenv("COACH_CONFIG_DIR", str(b))
    assert coach_paths.resolve_coach_dir() == b


def test_resolve_empty_env_falls_back(monkeypatch):
    """Empty string COACH_CONFIG_DIR is treated as unset (falsy guard)."""
    monkeypatch.setenv("COACH_CONFIG_DIR", "")
    assert coach_paths.resolve_coach_dir() == Path.home() / ".claude" / "coach"


def test_user_config_delegates_to_coach_paths(tmp_path, monkeypatch):
    """user_config._resolve_config_path() should call into the shared
    helper so env-var contract is enforced in one place. Verified
    behaviorally: setting COACH_CONFIG_DIR redirects user_config writes
    to the same dir that coach_paths reports."""
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path))
    import user_config
    assert user_config._resolve_config_path() == tmp_path / ".user_config.json"
    assert coach_paths.resolve_coach_dir() == tmp_path
