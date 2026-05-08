"""Statusline render path is fail-soft.

CLAUDE.md treats hook fail-soft as non-negotiable; the statusline shares
the same property pragmatically — it runs on every Claude Code render,
and a traceback there both blanks the prefix and noises up the user's
terminal. These tests pin both `default_statusline.main()` and
`stats.main()` to swallow exceptions and exit 0, matching the hook
fail-soft contract in `hooks/coach-session-start.py`.
"""
from __future__ import annotations

import io
import json
import sys

import pytest


def test_default_statusline_main_failsafe_on_render_exception(monkeypatch, capsys):
    """If render_segment raises, default_statusline.main returns 0 — no traceback."""
    import default_statusline as ds

    def boom(*_a, **_kw):
        raise RuntimeError("simulated corrupt profile mid-write")

    monkeypatch.setattr(ds, "render_segment", boom)

    payload = json.dumps({
        "model": {"display_name": "Claude Sonnet 4.6"},
        "context_window": {"used_percentage": 42},
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    rc = ds.main()
    assert rc == 0
    captured = capsys.readouterr()
    # The prefix may or may not have been flushed before the exception;
    # what matters is no traceback escaped to stderr.
    assert "Traceback" not in captured.err
    assert "RuntimeError" not in captured.err


def test_stats_main_failsafe_on_render_exception(monkeypatch, capsys):
    """If render_segment raises, stats.main returns 0 — no traceback."""
    import stats

    def boom(*_a, **_kw):
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(stats, "render_segment", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    rc = stats.main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "RuntimeError" not in captured.err


def test_default_statusline_main_failsafe_on_corrupt_stdin(monkeypatch, capsys):
    """A non-JSON stdin payload still exits 0 — covered by _read_stdin_payload's
    own try/except, but pinned here as the user-visible contract."""
    import default_statusline as ds

    monkeypatch.setattr("sys.stdin", io.StringIO("{ not valid json"))

    rc = ds.main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
