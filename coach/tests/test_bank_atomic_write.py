"""bank.py — atomic write unlinks tmp on os.replace failure.

Pinned regression for the unlink-on-failure path in
`bank._atomic_write_yaml`. Before the fix, a rare cross-device or
filesystem-quirk failure of `os.replace` would leak a stale
`.profile.<rand>.tmp` into `~/.claude/coach/`, eventually accumulating
visible junk. This test forces the failure path and asserts the temp
is cleaned up. Mirrors the cleanup pattern in
`merge.atomic_write_yaml` and `marker_io._atomic_write_under_lock`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import bank


def test_atomic_write_unlinks_temp_on_replace_failure(tmp_path, monkeypatch):
    """If os.replace raises, _atomic_write_yaml must unlink the .tmp and re-raise."""
    target = tmp_path / "profile.yaml"
    target.write_text("schema_version: 1\nentries: []\n")

    captured: list[str] = []

    def failing_replace(src, _dst):
        captured.append(src)
        raise OSError("simulated cross-device rename failure")

    monkeypatch.setattr(bank.os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated cross-device rename failure"):
        bank._atomic_write_yaml(target, {"schema_version": 1, "entries": []})

    # The exception must propagate (caller decides what to do).
    assert captured, "os.replace was never called — test setup wrong"
    leftover = captured[0]
    assert not Path(leftover).exists(), (
        f"tmp file {leftover} was not unlinked after os.replace failure — "
        "matches the bug class merge.atomic_write_yaml fixed; ensure "
        "bank._atomic_write_yaml mirrors that pattern."
    )

    # No stray .profile.*.tmp in the directory either.
    stragglers = sorted(p.name for p in tmp_path.glob(".profile.*.tmp"))
    assert stragglers == [], f"leftover tmp files: {stragglers}"


def test_atomic_write_happy_path_still_works(tmp_path):
    """Round-trip on the happy path — confirm the unlink-on-failure
    addition didn't break the normal write."""
    target = tmp_path / "profile.yaml"
    bank._atomic_write_yaml(target, {"schema_version": 1, "entries": []})
    assert target.exists()
    content = target.read_text()
    assert "schema_version: 1" in content
    assert "entries: []" in content
    # No stray tmp files.
    assert sorted(p.name for p in tmp_path.glob(".profile.*.tmp")) == []
