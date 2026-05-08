#!/usr/bin/env python3
"""Run a command while holding an exclusive flock on a sidecar file.

Used by insights-llm.sh to serialize concurrent weekly-insights runs.
Two Claude Code SessionStart hooks firing within the ~90s window of a
slow `claude -p "/insights"` call will both see `.last_weekly_insights`
as stale and try to spawn the wrapper. Without this lock, both run the
LLM call, both aggregate, both merge — wasting an LLM call and
prematurely advancing debounce/graduation streaks. Under the lock, the
second wrapper rechecks the throttle (which the first wrapper just
refreshed) and skips cleanly.

Usage:
  run_with_lock.py <lock_path> <cmd> [args...]

Behavior:
  - Acquires `fcntl.LOCK_EX | LOCK_NB` on the lock file (created if
    absent). Failure to acquire (because another process holds it)
    prints a one-line "skipped (concurrent ...)" notice to stdout and
    exits SKIP_EXIT_CODE (10). The caller should treat this as
    benign — coordination, not error.
  - On success, runs `cmd` as a subprocess and returns its exit code.
    Sets `COACH_LLM_LOCK_HELD=1` in the child env so the wrapped
    script can detect it's already inside the lock and skip a
    re-exec loop.
  - Lock auto-releases when this process exits (per fcntl semantics);
    we also explicitly unlock + close on the way out.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys


SKIP_EXIT_CODE = 10


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: run_with_lock.py <lock_path> <cmd> [args...]",
            file=sys.stderr,
        )
        return 64

    lock_path = sys.argv[1]
    cmd = sys.argv[2:]

    # Ensure the lock file's parent dir exists (test fixtures sometimes
    # point at not-yet-created paths).
    parent = os.path.dirname(lock_path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print("skipped (concurrent weekly run in progress)")
        return SKIP_EXIT_CODE

    env = os.environ.copy()
    env.setdefault("COACH_LLM_LOCK_HELD", "1")

    try:
        proc = subprocess.run(cmd, env=env)
        return proc.returncode
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
