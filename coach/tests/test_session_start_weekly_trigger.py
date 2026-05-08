"""Tests for the SessionStart hook's weekly insights trigger.

Covers _maybe_spawn_weekly_insights — the 7-day-stale check that decides
whether to fork insights-llm.sh on session start. The wrapper itself
enforces the throttle inside its own logic; the hook just avoids the fork
when we already know it would skip.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parent.parent.parent / "hooks" / "coach-session-start.py"


@pytest.fixture
def hook_module(tmp_path, monkeypatch):
    """Load the hook source against a tmp COACH_DIR.

    The hook resolves COACH_DIR = Path.home() / ".claude" / "coach" at
    module-load time, so we patch Path.home() to return tmp_path BEFORE
    exec'ing the module, then create the matching dir tree under it.
    """
    coach_dir = tmp_path / ".claude" / "coach"
    bin_dir = coach_dir / "bin"
    bin_dir.mkdir(parents=True)

    invocation_log = tmp_path / "invocation.log"
    fake_script = bin_dir / "insights-llm.sh"
    fake_script.write_text(
        f"#!/bin/bash\necho fired > '{invocation_log}'\n"
    )
    fake_script.chmod(0o755)

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    spec = importlib.util.spec_from_file_location("coach_session_start", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["coach_session_start"] = module
    spec.loader.exec_module(module)

    return module, coach_dir, invocation_log


def test_spawns_when_marker_missing(hook_module) -> None:
    module, coach_dir, invocation_log = hook_module
    assert not (coach_dir / ".last_weekly_insights").exists()
    module._maybe_spawn_weekly_insights(datetime.now(timezone.utc))
    # Wait briefly for the detached subprocess to finish writing.
    for _ in range(20):
        if invocation_log.exists():
            break
        time.sleep(0.1)
    assert invocation_log.exists(), "expected wrapper to be spawned"


def test_skips_when_marker_recent(hook_module) -> None:
    module, coach_dir, invocation_log = hook_module
    marker = coach_dir / ".last_weekly_insights"
    marker.touch()
    module._maybe_spawn_weekly_insights(datetime.now(timezone.utc))
    # No fork should happen — wait, then assert nothing fired.
    time.sleep(0.5)
    assert not invocation_log.exists(), "wrapper was spawned despite recent marker"


def test_spawns_when_marker_stale(hook_module) -> None:
    module, coach_dir, invocation_log = hook_module
    marker = coach_dir / ".last_weekly_insights"
    marker.touch()
    stale_ts = time.time() - 8 * 86400
    os.utime(marker, (stale_ts, stale_ts))
    module._maybe_spawn_weekly_insights(datetime.now(timezone.utc))
    for _ in range(20):
        if invocation_log.exists():
            break
        time.sleep(0.1)
    assert invocation_log.exists(), "expected wrapper to fire on stale marker"


def test_no_crash_when_script_missing(hook_module, tmp_path) -> None:
    module, coach_dir, _ = hook_module
    # Remove the script — the hook must still return cleanly.
    (coach_dir / "bin" / "insights-llm.sh").unlink()
    module._maybe_spawn_weekly_insights(datetime.now(timezone.utc))
    # No exception is the assertion.
