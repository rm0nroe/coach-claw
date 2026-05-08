#!/usr/bin/env python3
"""
Convert completed-session XP into lifetime XP at a 10:1 discount.

Invoked from the SessionStart hook (and runnable manually for testing).
For each main session transcript that:
  - has been inactive for >= COOLDOWN_MINUTES (i.e. the session is over), and
  - has not already been banked (not in banked_sessions.json)
...compute that session's XP with the same scoring as stats.py, then:
  lifetime_gain = floor(session_xp / 10)
  profile.session_banked_xp += lifetime_gain
  banked_sessions[session_id] = {"xp": session_xp, "banked": lifetime_gain, "at": ISO}

Scoring (must match stats.py):
  +2 per test-runner Bash invocation (pytest/jest/cargo/go test/...)
  +1 per `git commit`
  +1 per unique slash-command / skill invoked
  session XP capped at 15

Design invariants:
  - Always exits 0. Never blocks a session start.
  - Uses flock to avoid racing with /coach-insights's merge.py on profile.yaml.
  - Writes profile.yaml atomically (tempfile + os.replace).
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xp_accounting import add_session_banked_xp, normalize_profile_xp  # noqa: E402
from coach_paths import resolve_coach_dir  # noqa: E402

COACH_DIR = resolve_coach_dir()
PROFILE = COACH_DIR / "profile.yaml"
LOCK = COACH_DIR / ".lock"
LEDGER = COACH_DIR / "banked_sessions.json"
PROJECTS = Path.home() / ".claude" / "projects"

# Session is considered "done" once its transcript has been untouched for
# this long. Short enough that a normally-ended session banks promptly,
# long enough that a paused session isn't prematurely banked.
COOLDOWN_MINUTES = 30
SCAN_WINDOW_DAYS = 7
SESSION_XP_CAP = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _load_ledger() -> dict:
    if not LEDGER.exists():
        return {}
    try:
        data = json.loads(LEDGER.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_ledger(ledger: dict) -> None:
    """Atomic write — a crash mid-write must not corrupt banked-session
    state. The main flock is held by the caller, so this only needs to
    guard against partial writes (e.g. process kill, disk-full half-write).
    """
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".banked_sessions.", suffix=".tmp", dir=str(LEDGER.parent)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(ledger, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, LEDGER)
        except Exception:
            try:
                os.unlink(tmp_name)
            except Exception:
                pass
            raise
    except Exception:
        pass


def _score_transcript(path: Path, profile: dict | None = None) -> int:
    """Score one session's transcript. Delegates to scoring.py so stats.py
    and bank.py can never drift — one source of truth for action detection
    and baseline XP. `profile` is the already-loaded yaml; when present,
    dynamic reward_hint actions also score."""
    from scoring import score_transcript
    return score_transcript(path, profile)


def _recent_main_transcripts() -> list[Path]:
    if not PROJECTS.exists():
        return []
    cutoff = _now().timestamp() - SCAN_WINDOW_DAYS * 86400
    out: list[Path] = []
    for p in PROJECTS.rglob("*.jsonl"):
        if "/subagents/" in str(p):
            continue
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except Exception:
            continue
    return out


def _atomic_write_yaml(path: Path, data: dict) -> None:
    import yaml
    tmp = tempfile.NamedTemporaryFile(
        "w", delete=False, dir=path.parent, prefix=".profile.", suffix=".tmp"
    )
    try:
        yaml.safe_dump(data, tmp, sort_keys=False, default_flow_style=False, allow_unicode=True)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    try:
        os.replace(tmp.name, path)
    except Exception:
        # Mirrors merge.atomic_write_yaml + marker_io._atomic_write_under_lock:
        # don't leak an orphan .profile.*.tmp on a rare cross-device or
        # filesystem-quirk failure of os.replace.
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise


def _bank() -> dict:
    """Do the actual banking work. Returns a summary dict."""
    summary = {"scanned": 0, "skipped_active": 0, "already_banked": 0, "banked": 0, "xp_added": 0}

    if not PROFILE.exists():
        return summary

    try:
        import yaml
        profile = yaml.safe_load(PROFILE.read_text()) or {}
    except Exception:
        return summary
    if not isinstance(profile, dict):
        return summary
    needs_migration_write = "session_banked_xp" not in profile
    normalize_profile_xp(profile)

    ledger = _load_ledger()
    now = _now()
    cooldown_seconds = COOLDOWN_MINUTES * 60

    total_gain = 0
    for t in _recent_main_transcripts():
        summary["scanned"] += 1
        session_id = t.stem  # uuid
        if session_id in ledger:
            summary["already_banked"] += 1
            continue
        try:
            age = now.timestamp() - t.stat().st_mtime
        except Exception:
            continue
        if age < cooldown_seconds:
            summary["skipped_active"] += 1
            continue

        session_xp = _score_transcript(t, profile)
        lifetime_gain = session_xp // 10
        ledger[session_id] = {
            "xp": session_xp,
            "banked": lifetime_gain,
            "at": _iso(now),
            "transcript": str(t),
        }
        total_gain += lifetime_gain
        summary["banked"] += 1

    if total_gain > 0:
        add_session_banked_xp(profile, total_gain)
        summary["xp_added"] = total_gain
    if total_gain > 0 or needs_migration_write:
        _atomic_write_yaml(PROFILE, profile)

    _save_ledger(ledger)
    return summary


# Max seconds to wait for the profile.yaml lock before giving up. /coach-insights
# typically completes in well under a second; 30s is a comfortable ceiling
# that lets us absorb an unusually slow run without dropping session XP.
# Bounded-wait (not infinite) so a stuck lock file can't keep bank.py alive
# forever. SessionStart spawns bank.py detached, so waiting here does NOT
# delay the hook — safe to block.
LOCK_WAIT_SECONDS = 30
LOCK_RETRY_INTERVAL = 0.5


def _acquire_lock_bounded(lockfile) -> bool:
    """Try LOCK_EX up to LOCK_WAIT_SECONDS. Returns True on acquire, False
    if we timed out (profile is still being merged by /coach-insights — next
    SessionStart will pick up the missed banking)."""
    import time
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(LOCK_RETRY_INTERVAL)


def main() -> int:
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        with LOCK.open("a+") as lockfile:
            if not _acquire_lock_bounded(lockfile):
                # /coach-insights has been holding the lock for >30s — exceptional.
                # Bail; the transcript is still on disk and within the 7-day
                # scan window, so the next SessionStart will bank it.
                return 0
            summary = _bank()
        if "--verbose" in sys.argv:
            print(json.dumps(summary, indent=2))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
