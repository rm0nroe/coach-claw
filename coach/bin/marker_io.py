"""Atomic, locked I/O for celebration marker files.

Single source of truth for marker writes. Used by `merge.py` (graduation,
regression, streak markers) and `stats.py` (levelup marker). The
UserPromptSubmit hook's `_read_and_consume()` takes the SAME sidecar
flock (`<path>.lock`), so reader/writer interleaves are serialized:

  • A reader holding the lock won't see a half-written marker.
  • A writer holding the lock can't be clobbered by a reader's
    atomic-replace landing after a stale read.

Why this module exists: the v0.2.0 release introduced `consumed_by`
tracking on the read side but left writers unlocked, so a stale-snapshot
reader could overwrite a freshly-appended writer payload and silently
drop an event. This module closes that gap.

Helpers never raise — celebration markers are UX, not correctness, and a
write failure must never break `/coach-insights` or the statusline.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


def _lock_path_for(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _atomic_write_under_lock(path: Path, payload: dict) -> None:
    """tempfile + os.replace inside the caller's flock context.

    Caller MUST already hold the sidecar lock. This helper is intentionally
    private — public surface goes through `atomic_marker_rmw_append` or
    `atomic_marker_replace` so the lock-acquisition is not optional.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(payload))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise


def atomic_marker_rmw_append(
    path: Path,
    items_key: str,
    new_items: list[dict],
    now: datetime,
) -> None:
    """Read-modify-write append for celebration markers.

    Acquires the sidecar flock, reads any existing items under that key,
    appends `new_items`, and atomic-replaces the file. Resets `consumed_by`
    to [] and `created_at` to `now` on every successful write — this is
    deliberate: if `/coach-insights` runs twice in <MARKER_TTL_HOURS, the second
    batch is genuinely new news and a session that already consumed the
    prior version still needs to see the additions. The cost is a one-time
    re-render of prior entries, which is the right trade vs missing the
    new ones entirely.

    `oldest_entry_at` is preserved across appends (never reset) so the
    catch-up framing line in `<coach-celebrate>` correctly fires when an
    older queued entry is carried into a fresh append. `created_at`
    continues to track the latest write (drives the 24h TTL on the read
    side); `oldest_entry_at` tracks the first unconsumed write. On the
    first append against a legacy marker (pre-v0.4.2, no
    `oldest_entry_at`), the existing `created_at` is promoted into
    `oldest_entry_at` so carried-over entries still drive catch-up.

    Args:
        path: marker file path (e.g. ~/.claude/coach/.pending_graduation)
        items_key: top-level key in the JSON payload (e.g. "graduations")
        new_items: list of dicts to append
        now: timestamp for the new `created_at`
    """
    if not new_items:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(_lock_path_for(path), "w") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                # flock unsupported on this fs — proceed best-effort.
                # Worst case is the legacy race; we still atomic-replace.
                pass
            existing: list[dict] = []
            oldest_entry_at: str | None = None
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    if isinstance(data, dict):
                        prior = data.get(items_key)
                        if isinstance(prior, list):
                            existing = [x for x in prior if isinstance(x, dict)]
                        prior_oldest = data.get("oldest_entry_at")
                        if isinstance(prior_oldest, str):
                            oldest_entry_at = prior_oldest
                        elif isinstance(data.get("created_at"), str):
                            # Legacy marker (pre-v0.4.2): promote existing
                            # created_at so carried-over entries still
                            # drive catch-up framing.
                            oldest_entry_at = data["created_at"]
                except Exception:
                    existing = []
            if oldest_entry_at is None:
                # Fresh marker (or unreadable prior file) — anchor at now.
                oldest_entry_at = now.isoformat()
            payload = {
                items_key: existing + new_items,
                "created_at": now.isoformat(),
                "oldest_entry_at": oldest_entry_at,
                "consumed_by": [],
            }
            _atomic_write_under_lock(path, payload)
    except Exception:
        pass


def atomic_marker_replace(path: Path, payload: dict) -> None:
    """Atomic full-replace for single-event markers (e.g. levelup).

    Levelup is a high-water-mark event — each new level-up replaces any
    prior payload rather than merging. The lock is still required so
    reader/writer interleave doesn't drop a fresh level-up announcement.
    Caller is responsible for setting `created_at` and `consumed_by` on
    the payload.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(_lock_path_for(path), "w") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            _atomic_write_under_lock(path, payload)
    except Exception:
        pass
