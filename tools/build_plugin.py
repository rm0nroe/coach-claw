#!/usr/bin/env python3
"""Build the Claude Code plugin variant from canonical CLI sources.

The npm CLI is the source-of-truth. This script copies the Python core
(`coach/bin/*.py`) and hook entry points (`hooks/coach-*.py`) into the
plugin's `bin/` and `hooks/` dirs, where they get distributed via the
plugin marketplace.

Skills are NOT generated — they're hand-maintained under `plugin/skills/`
because the plugin variants diverge meaningfully from the CLI ones
(namespaced commands, different uninstall story, ${CLAUDE_PLUGIN_ROOT}
paths). The CLI skill source-of-truth stays at `skills/`.

Run from the repo root:

    python3 tools/build_plugin.py

The result is a `plugin/` dir installable via:

    /plugin install coach-claw@/abs/path/to/this-repo/plugin

`coach/tests/test_plugin_synced.py` enforces that the committed
`plugin/bin/` and `plugin/hooks/` byte-match the canonical sources, so
forgetting to run this script before committing fails CI.
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Source dirs (canonical) → destination dirs (plugin layout).
COPY_PAIRS: list[tuple[str, str, list[str]]] = [
    # (src_subdir, dst_subdir, glob patterns)
    ("coach/bin", "plugin/bin", ["*.py", "*.sh"]),
    ("hooks", "plugin/hooks", ["coach-*.py"]),
]

# Files that legitimately live ONLY in the plugin layout (no canonical
# CLI counterpart). The sweep must NOT delete these. Mirrors the
# allowlist in coach/tests/test_plugin_synced.py — keep both in sync.
PLUGIN_ONLY = {
    "bootstrap.sh",  # hook wrapper: coexistence guard + delegate to run.sh
    "run.sh",        # skill wrapper: PyYAML venv setup + python exec
}


def collect_sources(src: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pat in patterns:
        files.extend(sorted(src.glob(pat)))
    return files


def copy_tree(src: Path, dst: Path, patterns: list[str]) -> tuple[int, int]:
    """Copy files matching `patterns` from `src` to `dst`. Returns
    (copied, skipped_unchanged). Sweeps stale files in `dst` that match
    the same patterns but no longer exist in `src`, so the plugin dir
    is a faithful mirror of the matched file set. Files NOT matching
    the patterns (e.g. hand-authored manifests like hooks.json) are
    left untouched."""
    dst.mkdir(parents=True, exist_ok=True)
    sources = collect_sources(src, patterns)
    src_names = {f.name for f in sources}

    # Only sweep within the pattern set — leave hand-managed files alone.
    # Plugin-only files (e.g. bootstrap.sh) are explicitly preserved.
    pattern_matches_in_dst = collect_sources(dst, patterns)
    for existing in pattern_matches_in_dst:
        if existing.name in PLUGIN_ONLY:
            continue
        if existing.name not in src_names:
            existing.unlink()
            print(f"  removed (stale): {existing.relative_to(REPO_ROOT)}")

    copied = 0
    skipped = 0
    for f in sources:
        target = dst / f.name
        if target.exists() and filecmp.cmp(f, target, shallow=False):
            skipped += 1
            continue
        shutil.copy2(f, target)
        copied += 1
        print(f"  copied:          {target.relative_to(REPO_ROOT)}")
    return copied, skipped


def main() -> int:
    if not (REPO_ROOT / "coach" / "bin").is_dir():
        print(f"error: not a coach-claw repo root: {REPO_ROOT}", file=sys.stderr)
        return 2

    total_copied = 0
    total_skipped = 0
    for src_sub, dst_sub, patterns in COPY_PAIRS:
        src = REPO_ROOT / src_sub
        dst = REPO_ROOT / dst_sub
        print(f"{src_sub} → {dst_sub}")
        c, s = copy_tree(src, dst, patterns)
        total_copied += c
        total_skipped += s

    # Ensure plugin/bin executable bits on .sh files (shutil.copy2 preserves
    # mode but it can't hurt to enforce in case the source was non-exec).
    for sh in (REPO_ROOT / "plugin" / "bin").glob("*.sh"):
        sh.chmod(sh.stat().st_mode | 0o111)

    print()
    print(f"build complete: {total_copied} copied, {total_skipped} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
