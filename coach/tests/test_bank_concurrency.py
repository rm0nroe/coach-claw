"""bank.py concurrency — bounded-wait lock acquire.

Regression guard for the race where /coach-insights holds the profile lock at
the moment SessionStart spawns bank.py. Before this fix, bank.py would
non-blocking-fail immediately and silently drop the session's XP banking
until the NEXT SessionStart. Now bank.py waits up to LOCK_WAIT_SECONDS
for the lock to free up.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path

import pytest
import yaml

import bank


def _hold_lock_for(path: Path, seconds: float, released: threading.Event) -> None:
    """Acquire the lock, hold it for `seconds`, release, signal."""
    with path.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        time.sleep(seconds)
        fcntl.flock(fh, fcntl.LOCK_UN)
    released.set()


def test_lock_acquired_immediately_when_free(tmp_path, monkeypatch):
    """When nothing holds the lock, bank.py acquires essentially instantly."""
    monkeypatch.setattr(bank, "LOCK_WAIT_SECONDS", 5)
    monkeypatch.setattr(bank, "LOCK_RETRY_INTERVAL", 0.05)
    lockfile_path = tmp_path / ".lock"

    start = time.monotonic()
    with lockfile_path.open("a+") as fh:
        assert bank._acquire_lock_bounded(fh) is True
    elapsed = time.monotonic() - start
    assert elapsed < 0.2   # essentially immediate


def test_lock_waits_then_succeeds_when_released(tmp_path, monkeypatch):
    """If the lock frees up before the bounded-wait timeout, bank.py
    acquires it and proceeds — the case that fixes the race."""
    monkeypatch.setattr(bank, "LOCK_WAIT_SECONDS", 3)
    monkeypatch.setattr(bank, "LOCK_RETRY_INTERVAL", 0.05)
    lockfile_path = tmp_path / ".lock"
    released = threading.Event()

    # Hold the lock for 0.4s from a background thread.
    holder = threading.Thread(
        target=_hold_lock_for, args=(lockfile_path, 0.4, released), daemon=True,
    )
    holder.start()
    time.sleep(0.05)   # ensure holder grabbed the lock first

    start = time.monotonic()
    with lockfile_path.open("a+") as fh:
        acquired = bank._acquire_lock_bounded(fh)
    elapsed = time.monotonic() - start

    holder.join(timeout=2)
    assert acquired is True
    assert elapsed >= 0.3            # did wait for the holder
    assert elapsed < 2.0             # but came back well before the timeout
    assert released.is_set()


def test_lock_gives_up_after_timeout(tmp_path, monkeypatch):
    """If the holder never releases, bank.py returns False after the
    bounded-wait deadline passes — safe fallback so a stuck lock never
    hangs the process indefinitely."""
    monkeypatch.setattr(bank, "LOCK_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(bank, "LOCK_RETRY_INTERVAL", 0.05)
    lockfile_path = tmp_path / ".lock"
    released = threading.Event()

    # Hold the lock longer than LOCK_WAIT_SECONDS.
    holder = threading.Thread(
        target=_hold_lock_for, args=(lockfile_path, 1.0, released), daemon=True,
    )
    holder.start()
    time.sleep(0.05)

    start = time.monotonic()
    with lockfile_path.open("a+") as fh:
        acquired = bank._acquire_lock_bounded(fh)
    elapsed = time.monotonic() - start

    holder.join(timeout=3)
    assert acquired is False
    assert 0.2 <= elapsed < 0.8      # gave up around the timeout, not much later


def test_bank_writes_session_banked_xp_not_milestones(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    ledger_path = tmp_path / "banked_sessions.json"
    projects = tmp_path / "projects"
    projects.mkdir()
    transcript = projects / "session-1.jsonl"
    transcript.write_text("{}\n")
    old = time.time() - 3600
    os.utime(transcript, (old, old))
    profile_path.write_text(yaml.safe_dump({
        "entries": [],
        "graduated": [],
        "milestone_xp": 3,
    }))
    monkeypatch.setattr(bank, "PROFILE", profile_path)
    monkeypatch.setattr(bank, "LEDGER", ledger_path)
    monkeypatch.setattr(bank, "PROJECTS", projects)
    monkeypatch.setattr(bank, "_score_transcript", lambda _path, _profile: 12)

    summary = bank._bank()

    written = yaml.safe_load(profile_path.read_text())
    ledger = json.loads(ledger_path.read_text())
    assert summary["xp_added"] == 1
    assert written["session_banked_xp"] == 1
    assert written["banked_session_xp"] == 1
    assert written["milestone_xp"] == 3
    assert ledger["session-1"]["banked"] == 1
