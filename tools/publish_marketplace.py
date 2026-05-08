#!/usr/bin/env python3
"""Publish marketplace/ contents to the standalone marketplace repo.

The marketplace lives in a SEPARATE GitHub repo from the plugin source
(`rm0nroe/coach-claw-plugin-marketplace`) so that:

  1. Users adding the marketplace via `/plugin marketplace add
     rm0nroe/coach-claw-plugin-marketplace` aren't forced to clone the
     whole 500-test plugin source repo.
  2. Marketplace versioning + the plugin's own version evolve
     independently — bumping plugin.json doesn't churn the marketplace.

Source-of-truth for marketplace content lives at `marketplace/` in this
repo. This script syncs that dir into a sibling clone of the
marketplace repo, then commits + pushes.

Prerequisites (one-time):

    # Create the GitHub repo:
    gh repo create rm0nroe/coach-claw-plugin-marketplace --public \\
        --description "Coach Claw plugin marketplace for Claude Code"

    # Clone next to coach-claw:
    git clone git@github.com:rm0nroe/coach-claw-plugin-marketplace.git \\
        ~/Desktop/coach-claw-plugin-marketplace

Usage:

    python3 tools/publish_marketplace.py [--dry-run] [--target PATH]

By default, target is ../coach-claw-plugin-marketplace relative to this
repo's parent dir. Override with --target if you cloned elsewhere.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "marketplace"
DEFAULT_TARGET = REPO_ROOT.parent / "coach-claw-plugin-marketplace"


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _validate_target(target: Path) -> None:
    if not target.exists():
        sys.exit(
            f"target dir {target} does not exist.\n"
            f"clone first:\n"
            f"  gh repo create rm0nroe/coach-claw-plugin-marketplace --public\n"
            f"  git clone git@github.com:rm0nroe/coach-claw-plugin-marketplace.git "
            f"{target}"
        )
    if not (target / ".git").is_dir():
        sys.exit(f"target {target} is not a git working tree")


def _copy_tree(src: Path, dst: Path, dry_run: bool) -> list[str]:
    """Mirror SRC into DST. Returns a list of human-readable change lines."""
    changes: list[str] = []

    # Files to copy
    src_files = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst) for p in dst.rglob("*") if p.is_file() and ".git" not in p.parts}

    for rel in sorted(src_files):
        s = src / rel
        d = dst / rel
        if d.exists() and filecmp.cmp(s, d, shallow=False):
            continue
        verb = "would copy" if dry_run else "copying"
        changes.append(f"{verb}: {rel}")
        if not dry_run:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)

    # Files to delete (present in dst, absent in src — except .git/* which we skip above)
    for rel in sorted(dst_files - src_files):
        verb = "would remove" if dry_run else "removing"
        changes.append(f"{verb}: {rel}")
        if not dry_run:
            (dst / rel).unlink()

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show diffs but don't write/commit/push")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET,
                        help=f"Path to marketplace repo clone (default: {DEFAULT_TARGET})")
    parser.add_argument("--no-push", action="store_true",
                        help="Commit but don't push (useful for review)")
    args = parser.parse_args()

    if not SOURCE.exists():
        sys.exit(f"source {SOURCE} not found — run from a coach-claw repo root")

    target = args.target.resolve()
    _validate_target(target)

    print(f"source: {SOURCE.relative_to(REPO_ROOT)}")
    print(f"target: {target}")
    print()

    changes = _copy_tree(SOURCE, target, args.dry_run)
    if not changes:
        print("no changes — marketplace target is already up to date.")
        return 0
    for line in changes:
        print(f"  {line}")
    print()

    if args.dry_run:
        print(f"dry-run complete; {len(changes)} change(s) would be applied.")
        return 0

    # Stage + commit. Use the source's git short SHA in the commit message
    # for traceability.
    src_sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT).stdout.strip()
    msg = f"sync marketplace from coach-claw@{src_sha}"

    _run(["git", "add", "-A"], cwd=target)
    status = _run(["git", "status", "--porcelain"], cwd=target).stdout.strip()
    if not status:
        print("staged tree is clean — nothing to commit.")
        return 0
    _run(["git", "commit", "-m", msg], cwd=target)
    print(f"committed: {msg}")

    if args.no_push:
        print("--no-push set; skipping push. Run `git push` in the target dir when ready.")
        return 0

    _run(["git", "push"], cwd=target)
    print("pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
