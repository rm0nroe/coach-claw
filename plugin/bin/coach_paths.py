"""Resolve the Coach Claw state directory.

Single source of truth for `~/.claude/coach/` path resolution. Honors the
`COACH_CONFIG_DIR` env var so:

  - tests can monkeypatch via `monkeypatch.setenv("COACH_CONFIG_DIR", ...)`,
  - the npm CLI wrapper can export `COACH_CONFIG_DIR` to match a custom
    `CLAUDE_DIR` install path,
  - the upcoming Claude Code plugin distribution can point its hooks at a
    plugin-managed data dir without forking any logic.

Resolution happens per-call, never cached. That's deliberate — the env
var may be set after import time (test setup, subprocess wrappers) and
caching would break that contract. Cost is negligible: a single
`os.environ.get` per call.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_coach_dir() -> Path:
    """Return the Coach Claw state directory.

    Honors `COACH_CONFIG_DIR` (overrides everything); falls back to
    `~/.claude/coach`. Caller is responsible for creating the directory
    if it doesn't exist — this helper only resolves the path.
    """
    base = os.environ.get("COACH_CONFIG_DIR")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "coach"
