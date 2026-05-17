#!/usr/bin/env python3
"""Coach Claw — plugin cache prune helper.

Claude Code's plugin system keeps every previously installed version
under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` after
a `/plugin update`. Only the version pointed at by
`~/.claude/plugins/installed_plugins.json` is active; older dirs are
cosmetic leftovers but accumulate disk space across releases.

This helper finds the active version from `installed_plugins.json`, then
removes any sibling version dirs strictly older by semver. Active dir is
never touched. Newer dirs (shouldn't exist, but defensive) never touched.

Race guard: a dir whose mtime is within `RECENT_MTIME_THRESHOLD_SECONDS`
(default 60s) is skipped. Covers the case where another session is
mid-install on a different version, or where `/plugin update` just wrote
a dir we'd otherwise misclassify as orphaned.

Failsafe: every public function wraps in try/except and returns an empty
list on any error. Cache prune is convenience, not correctness.

CLI:
    python3 cache_prune.py                # prune silently
    python3 cache_prune.py --verbose      # print per-dir result
    python3 cache_prune.py --dry-run      # show what would be removed
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

PLUGIN_NAME = "coach-claw"
MARKETPLACE = "coach-claw-plugins"
RECENT_MTIME_THRESHOLD_SECONDS = 60
# Number of predecessor versions to keep below the active one. Long-running
# Claude Code processes hold the plugin path they resolved at session start
# in memory; if /plugin update bumps several versions in rapid succession,
# the original version dir can drop out of an N-1 buffer before the user
# restarts CC. N-3 absorbs realistic multi-bump scenarios (e.g. a release
# train that ships 0.1.17→0.1.21 across one commit) without unbounded
# disk growth.
PREDECESSOR_RETENTION_COUNT = 3
INSTALLED_PLUGINS_JSON = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache" / MARKETPLACE / PLUGIN_NAME


def _parse_semver(s: str) -> tuple[int, ...] | None:
    """Plain dotted-int semver → tuple. Pre-release tags rejected (None)."""
    try:
        parts = s.split(".")
        return tuple(int(p) for p in parts)
    except (ValueError, AttributeError):
        return None


def find_active_version(installed_plugins_path: Path = INSTALLED_PLUGINS_JSON) -> str | None:
    """Read installed_plugins.json and return the active coach-claw version.

    Returns None if the file is missing, malformed, or has no
    coach-claw@coach-claw-plugins entry. Multiple entries (project +
    user scope) → return the highest version (user scope wins by
    semver, not by scope priority — we always prune to highest known).
    """
    try:
        data = json.loads(installed_plugins_path.read_text())
        entries = data.get("plugins", {}).get(
            f"{PLUGIN_NAME}@{MARKETPLACE}", []
        )
        if not entries:
            return None
        versions = []
        for e in entries:
            v = e.get("version")
            parsed = _parse_semver(v) if v else None
            if parsed:
                versions.append((parsed, v))
        if not versions:
            return None
        return max(versions, key=lambda pair: pair[0])[1]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def prune_inactive_cache_versions(
    cache_root: Path = CACHE_ROOT,
    installed_plugins_path: Path = INSTALLED_PLUGINS_JSON,
    dry_run: bool = False,
    verbose: bool = False,
    now: float | None = None,
) -> list[Path]:
    """Remove cache dirs older than the active version. Return paths removed.

    Args:
        cache_root: per-plugin cache root (e.g. ~/.claude/plugins/cache/
            coach-claw-plugins/coach-claw). Each subdir = one version.
        installed_plugins_path: path to installed_plugins.json
        dry_run: if True, skip rmtree but still return what *would* be removed
        verbose: if True, print one line per decision to stderr
        now: override "current time" for testing (default: time.time())
    """
    try:
        if not cache_root.exists():
            return []
        active = find_active_version(installed_plugins_path)
        if active is None:
            if verbose:
                print("cache_prune: no active version found, skipping", file=sys.stderr)
            return []
        active_tuple = _parse_semver(active)
        if active_tuple is None:
            return []
        cutoff = (now if now is not None else time.time()) - RECENT_MTIME_THRESHOLD_SECONDS

        # Keep the PREDECESSOR_RETENTION_COUNT newest predecessors below
        # the active version as a rollback buffer. Long-running CC
        # processes load plugin hook paths in memory at session start;
        # if auto-prune deletes a dir those processes still reference,
        # /clear and SessionStart events fire with ENOENT. A wider
        # buffer absorbs multi-bump release trains (e.g. 0.1.17→0.1.21
        # in one commit) where the original version drops out of an
        # N-1 window across several prune cycles.
        all_semver_dirs = sorted(
            (
                (_parse_semver(c.name), c)
                for c in cache_root.iterdir()
                if c.is_dir() and _parse_semver(c.name) is not None
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        predecessor_keep: set[tuple[int, ...]] = set()
        for t, _ in all_semver_dirs:
            if t < active_tuple:
                predecessor_keep.add(t)
                if len(predecessor_keep) >= PREDECESSOR_RETENTION_COUNT:
                    break

        removed: list[Path] = []
        for child in cache_root.iterdir():
            if not child.is_dir():
                continue
            child_tuple = _parse_semver(child.name)
            if child_tuple is None:
                # non-semver dir name — leave alone
                if verbose:
                    print(f"cache_prune: skip non-semver {child.name}", file=sys.stderr)
                continue
            if child_tuple >= active_tuple:
                # active version OR newer (shouldn't happen, but defensive)
                if verbose:
                    print(f"cache_prune: keep {child.name} (>= active {active})", file=sys.stderr)
                continue
            if child_tuple in predecessor_keep:
                # predecessor inside retention window — keep as rollback buffer
                if verbose:
                    print(f"cache_prune: keep {child.name} (predecessor rollback buffer)", file=sys.stderr)
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                if verbose:
                    print(f"cache_prune: skip {child.name} (mtime within {RECENT_MTIME_THRESHOLD_SECONDS}s)", file=sys.stderr)
                continue
            if dry_run:
                if verbose:
                    print(f"cache_prune: would remove {child}", file=sys.stderr)
                removed.append(child)
                continue
            try:
                shutil.rmtree(child)
                removed.append(child)
                if verbose:
                    print(f"cache_prune: removed {child}", file=sys.stderr)
            except OSError as e:
                if verbose:
                    print(f"cache_prune: failed {child}: {e}", file=sys.stderr)
                continue
        return removed
    except Exception as e:
        if verbose:
            print(f"cache_prune: failsafe caught {type(e).__name__}: {e}", file=sys.stderr)
        return []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="show what would be removed")
    p.add_argument("--verbose", "-v", action="store_true", help="print per-dir decision")
    args = p.parse_args(argv)
    removed = prune_inactive_cache_versions(dry_run=args.dry_run, verbose=args.verbose)
    if args.verbose or args.dry_run:
        verb = "would remove" if args.dry_run else "removed"
        print(f"cache_prune: {verb} {len(removed)} dir(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
