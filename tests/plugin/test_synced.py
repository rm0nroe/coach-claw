"""CI gate: plugin/bin/ and plugin/hooks/coach-*.py must byte-match
canonical sources.

`tools/build_plugin.py` mirrors `coach/bin/*.py`, `coach/bin/*.sh`, and
`hooks/coach-*.py` into the plugin layout. If a developer edits the
canonical source but forgets to re-run the build script, the plugin
ships stale code while CLI users get the fix. This test fails CI in
that scenario.

Manifest, hooks.json, and SKILL.md files are NOT checked here — those
are hand-maintained per the build script's contract.
"""
from __future__ import annotations

import filecmp
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_BIN = REPO_ROOT / "plugin" / "bin"
PLUGIN_HOOKS = REPO_ROOT / "plugin" / "hooks"
COACH_BIN = REPO_ROOT / "coach" / "bin"
HOOKS = REPO_ROOT / "hooks"

# Files that legitimately live ONLY in the plugin layout (no CLI
# counterpart). Add new entries here if a plugin-specific helper is
# introduced. Mirrors the same allowlist in tools/build_plugin.py.
PLUGIN_ONLY = {
    "bootstrap.sh",  # hook wrapper: coexistence guard + delegate to run.sh
    "run.sh",        # skill wrapper: PyYAML venv setup + python exec
}


def _expected_pairs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for pat in ("*.py", "*.sh"):
        for src in COACH_BIN.glob(pat):
            pairs.append((src, PLUGIN_BIN / src.name))
    for src in HOOKS.glob("coach-*.py"):
        pairs.append((src, PLUGIN_HOOKS / src.name))
    return pairs


def test_plugin_payload_byte_matches_canonical():
    diffs: list[str] = []
    missing: list[str] = []
    for src, dst in _expected_pairs():
        if not dst.exists():
            missing.append(f"{src.relative_to(REPO_ROOT)} → {dst.relative_to(REPO_ROOT)} (missing)")
            continue
        if not filecmp.cmp(src, dst, shallow=False):
            diffs.append(f"{src.relative_to(REPO_ROOT)} ≠ {dst.relative_to(REPO_ROOT)}")
    msg_parts: list[str] = []
    if missing:
        msg_parts.append("missing in plugin/:\n  " + "\n  ".join(missing))
    if diffs:
        msg_parts.append("byte-mismatch:\n  " + "\n  ".join(diffs))
    if msg_parts:
        msg = (
            "plugin payload out of sync with canonical sources. Run:\n\n"
            "  python3 tools/build_plugin.py\n\n"
            + "\n\n".join(msg_parts)
        )
        pytest.fail(msg)


def test_no_orphans_in_plugin_bin():
    """Every file in plugin/bin/ should have a counterpart in coach/bin/,
    OR be in the PLUGIN_ONLY allowlist. Catches forgotten cleanup if a
    canonical file is renamed/deleted."""
    canonical_names = {f.name for f in COACH_BIN.glob("*.py")} | {f.name for f in COACH_BIN.glob("*.sh")}
    for f in sorted(PLUGIN_BIN.iterdir()):
        if not f.is_file():
            continue
        if f.name in PLUGIN_ONLY:
            continue
        if f.name.endswith((".py", ".sh")):
            assert f.name in canonical_names, (
                f"plugin/bin/{f.name} has no counterpart in coach/bin/ — "
                f"likely a stale leftover from a rename or deletion. "
                f"Run `python3 tools/build_plugin.py` to sweep it. (If "
                f"this is a new plugin-only helper, add it to PLUGIN_ONLY "
                f"in this test file.)"
            )
