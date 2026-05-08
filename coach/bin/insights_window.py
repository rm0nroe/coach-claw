#!/usr/bin/env python3
"""Compute the set of recent JSONL transcripts for the /coach-insights window.

Replaces an earlier BSD `find -newermt "$SINCE_TS"` invocation that
read its timestamp argument in the host's *local* timezone. The Python
heredoc that produced the timestamp emitted UTC, so on any non-UTC
host the window was off by `tz_offset` hours every cron run — under-
counting or double-counting transcripts depending on the sign.

This module does the cutoff math entirely in Python with a UTC-aware
`datetime.now(timezone.utc)` and compares against `path.stat().st_mtime`
(POSIX seconds-since-epoch — also TZ-independent). The result is
correct regardless of the host's `TZ` env var.

Usage from the shell:

    python3 insights_window.py <projects_dir> <window>

…where `<window>` is `1d` / `7d` / `2h` / `30m` etc. Prints one
absolute path per line, sorted, and excludes `/subagents/` transcripts
(those are agent-tool spawns, not main sessions).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WINDOW_RE = re.compile(r"(\d+)([dhm])")


def parse_window(spec: str) -> timedelta:
    """Parse a window spec like ``1d`` / ``7d`` / ``2h`` / ``60m``.

    Raises ``ValueError`` on any other shape so the caller can surface
    a clear error instead of silently picking a default.
    """
    m = _WINDOW_RE.fullmatch(spec.strip())
    if not m:
        raise ValueError(f"bad window: {spec!r} (expected 1d/7d/2h/30m)")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "d": timedelta(days=n),
        "h": timedelta(hours=n),
        "m": timedelta(minutes=n),
    }[unit]


def cutoff_epoch(window: str, now: datetime | None = None) -> float:
    """Return the POSIX-epoch cutoff for the window.

    `now` may be supplied for testing; if given, it must be tz-aware
    so the cutoff is computed against an absolute moment in time.
    Defaults to ``datetime.now(timezone.utc)``.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    return (now - parse_window(window)).timestamp()


def recent_transcripts(
    projects_dir: Path,
    window: str,
    now: datetime | None = None,
) -> list[Path]:
    """Return main-session JSONL transcripts modified inside the window.

    The path-string filter for ``/subagents/`` matches the same exclusion
    used by ``bank.py:_recent_main_transcripts`` — the two callers share
    the same definition of "main session."
    """
    cutoff = cutoff_epoch(window, now)
    if not projects_dir.exists():
        return []
    out: list[Path] = []
    for p in projects_dir.rglob("*.jsonl"):
        if "/subagents/" in str(p):
            continue
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            # Permission denied / vanished mid-walk / etc — skip silently.
            continue
    return sorted(out)


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: insights_window.py <projects_dir> <window>",
            file=sys.stderr,
        )
        return 2
    projects_dir = Path(sys.argv[1])
    window = sys.argv[2]
    try:
        paths = recent_transcripts(projects_dir, window)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
