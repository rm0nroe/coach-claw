"""coach/bin/insights_window.py: TZ-correct /coach-insights window filter.

Pins the bug fix where BSD `find -newermt "$SINCE_TS"` was reading a
UTC-formatted timestamp in the host's *local* timezone, causing the
cron's 24h window to silently shrink or grow by tz_offset hours every
run on any non-UTC host.

These tests are deliberately TZ-parametrized: setting `TZ` at the
process level should NOT change the cutoff math, because we always
compute from `datetime.now(timezone.utc)` and compare against POSIX
`st_mtime` (both TZ-independent). If a future refactor reintroduces
a TZ-naive datetime or local-time formatting, one of these parametrize
cases will fail.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def iw():
    """Load coach/bin/insights_window.py as a module."""
    repo_path = Path(__file__).resolve().parents[2] / "coach" / "bin" / "insights_window.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "coach" / "bin" / "insights_window.py"
    if not path.exists():
        pytest.skip(f"insights_window.py not installed at {path}")
    spec = importlib.util.spec_from_file_location("iw_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- parse_window ----------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("1d", timedelta(days=1)),
    ("7d", timedelta(days=7)),
    ("2h", timedelta(hours=2)),
    ("30m", timedelta(minutes=30)),
    ("60m", timedelta(minutes=60)),
])
def test_parse_window_accepts_valid_specs(iw, spec, expected):
    assert iw.parse_window(spec) == expected


@pytest.mark.parametrize("bad", ["", "1", "d", "1w", "1.5h", "-1d", "abc"])
def test_parse_window_rejects_bad_specs(iw, bad):
    with pytest.raises(ValueError):
        iw.parse_window(bad)


# --- cutoff_epoch ----------------------------------------------------------

def test_cutoff_epoch_with_frozen_now(iw):
    now = datetime(2026, 5, 2, 11, 0, 0, tzinfo=timezone.utc)
    cutoff = iw.cutoff_epoch("1d", now=now)
    expected = now - timedelta(days=1)
    assert cutoff == expected.timestamp()


def test_cutoff_epoch_requires_tz_aware_now(iw):
    naive = datetime(2026, 5, 2, 11, 0, 0)  # no tzinfo
    with pytest.raises(ValueError):
        iw.cutoff_epoch("1d", now=naive)


@pytest.mark.parametrize("tz", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_cutoff_epoch_is_tz_independent(iw, tz, monkeypatch):
    """The cutoff should be the same absolute moment regardless of host TZ.
    Setting TZ in the env must not shift the result — that was the whole
    point of moving off `find -newermt`."""
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        now = datetime(2026, 5, 2, 11, 0, 0, tzinfo=timezone.utc)
        cutoff = iw.cutoff_epoch("1d", now=now)
        # Same UTC moment → same epoch, regardless of TZ env.
        assert cutoff == (now - timedelta(days=1)).timestamp()
    finally:
        # Restore process TZ so other tests aren't affected.
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# --- recent_transcripts ----------------------------------------------------

def _seed_transcript(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    os.utime(path, (mtime, mtime))
    return path


def test_recent_transcripts_filters_by_mtime(iw, tmp_path):
    now = datetime(2026, 5, 2, 11, 0, 0, tzinfo=timezone.utc)
    fresh = _seed_transcript(
        tmp_path / "p1" / "fresh.jsonl",
        (now - timedelta(hours=1)).timestamp(),
    )
    stale = _seed_transcript(
        tmp_path / "p1" / "stale.jsonl",
        (now - timedelta(days=2)).timestamp(),
    )
    out = iw.recent_transcripts(tmp_path, "1d", now=now)
    assert fresh in out
    assert stale not in out


def test_recent_transcripts_excludes_subagents(iw, tmp_path):
    now = datetime(2026, 5, 2, 11, 0, 0, tzinfo=timezone.utc)
    main = _seed_transcript(
        tmp_path / "p1" / "main.jsonl",
        (now - timedelta(hours=1)).timestamp(),
    )
    sub = _seed_transcript(
        tmp_path / "p1" / "subagents" / "spawn.jsonl",
        (now - timedelta(hours=1)).timestamp(),
    )
    out = iw.recent_transcripts(tmp_path, "1d", now=now)
    assert main in out
    assert sub not in out


def test_recent_transcripts_handles_missing_dir(iw, tmp_path):
    missing = tmp_path / "does-not-exist"
    assert iw.recent_transcripts(missing, "1d") == []


def test_recent_transcripts_validates_window_even_when_dir_missing(iw, tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError):
        iw.recent_transcripts(missing, "1week")


@pytest.mark.parametrize("tz", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_recent_transcripts_window_is_tz_independent(iw, tmp_path, tz, monkeypatch):
    """End-to-end TZ regression: a transcript whose mtime is 23h before
    `now` must always land inside a 1d window, regardless of host TZ.
    The previous `find -newermt` implementation failed this on
    Asia/Tokyo / America/Los_Angeles / any non-UTC host."""
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        now = datetime(2026, 5, 2, 11, 0, 0, tzinfo=timezone.utc)
        in_window = _seed_transcript(
            tmp_path / "p1" / "23h.jsonl",
            (now - timedelta(hours=23)).timestamp(),
        )
        out_of_window = _seed_transcript(
            tmp_path / "p1" / "25h.jsonl",
            (now - timedelta(hours=25)).timestamp(),
        )
        out = iw.recent_transcripts(tmp_path, "1d", now=now)
        assert in_window in out, f"23h-old transcript missed in TZ={tz}"
        assert out_of_window not in out, f"25h-old transcript included in TZ={tz}"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# --- subprocess smoke ------------------------------------------------------

def test_main_prints_paths_one_per_line(tmp_path):
    """Smoke-test the CLI entrypoint that insights.sh actually invokes."""
    repo_path = Path(__file__).resolve().parents[2] / "coach" / "bin" / "insights_window.py"
    if not repo_path.exists():
        pytest.skip("insights_window.py not present in repo")
    now = datetime.now(timezone.utc)
    fresh = _seed_transcript(
        tmp_path / "p1" / "fresh.jsonl",
        (now - timedelta(hours=1)).timestamp(),
    )
    stale = _seed_transcript(
        tmp_path / "p1" / "stale.jsonl",
        (now - timedelta(days=2)).timestamp(),
    )
    result = subprocess.run(
        [sys.executable, str(repo_path), str(tmp_path), "1d"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [ln for ln in result.stdout.strip().splitlines() if ln]
    assert str(fresh) in lines
    assert str(stale) not in lines


def test_main_rejects_bad_window():
    repo_path = Path(__file__).resolve().parents[2] / "coach" / "bin" / "insights_window.py"
    if not repo_path.exists():
        pytest.skip("insights_window.py not present in repo")
    result = subprocess.run(
        [sys.executable, str(repo_path), "/tmp", "1week"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "bad window" in result.stderr


def test_insights_sh_propagates_window_helper_failure(tmp_path):
    """insights.sh must fail when insights_window.py rejects the window.

    Bash process substitution hides producer exit statuses, so this pins
    the shell integration rather than only the Python helper behavior.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "coach" / "bin" / "insights.sh"
    helper = repo_root / "coach" / "bin" / "insights_window.py"
    if not script.exists() or not helper.exists():
        pytest.skip("insights runner not present in repo")

    home = tmp_path / "home"
    bin_dir = home / ".claude" / "coach" / "bin"
    projects = home / ".claude" / "projects"
    bin_dir.mkdir(parents=True)
    projects.mkdir(parents=True)
    (bin_dir / "insights.sh").write_text(script.read_text())
    (bin_dir / "insights_window.py").write_text(helper.read_text())

    result = subprocess.run(
        ["bash", str(bin_dir / "insights.sh"), "1week"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "bad window" in result.stderr
    assert "done" not in result.stdout
