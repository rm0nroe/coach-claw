"""Detect whether the npm CLI distribution has Coach hooks registered
in ~/.claude/settings.json, so the plugin's hooks can self-defer.

Plugin distribution only — invoked from plugin/bin/bootstrap.sh BEFORE
any venv setup or Python entry-point exec, so the check is cheap and
the defer path is fast.

Why this exists:

    npm CLI registers hooks in settings.json:
        SessionStart     -> ~/.claude/hooks/coach-session-start.py
        UserPromptSubmit -> ~/.claude/hooks/coach-user-prompt.py

    Plugin registers hooks in plugin/hooks/hooks.json:
        SessionStart     -> ${CLAUDE_PLUGIN_ROOT}/hooks/coach-session-start.py
        UserPromptSubmit -> ${CLAUDE_PLUGIN_ROOT}/hooks/coach-user-prompt.py

    A user with both installs gets 2x SessionStart + 2x UserPromptSubmit
    fires per event. bank.py runs twice (double XP). Tips render twice.
    `_assemble_celebrate_block` consumes a marker, plugin then re-reads
    cleared state — depends on which hook lands first; flaky.

The defer rule (CLI wins) is deliberate: the CLI is the canonical
provider-agnostic distribution, manages OS-side bits the plugin can't
reach (launchd cron, statusLine), and was likely installed first by
the user. Plugin self-disables until the user runs `/coach-claw:switch`.

Exit codes:
    0  — no CLI hooks detected; plugin should proceed.
    10 — CLI hooks detected; bootstrap.sh should `exit 0` without
         exec-ing the wrapped Python entry.

Always exits 0 on error (malformed settings.json, missing file). The
worst case is double-fire, not a broken hook.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFER_EXIT = 10
HOOK_SCRIPT_NAMES = ("coach-session-start.py", "coach-user-prompt.py")


def _coach_dir() -> Path:
    """Mirror coach_paths.resolve_coach_dir() — duplicated here to keep
    this script importable BEFORE the venv exists (and hence before the
    plugin's bin/ is on sys.path)."""
    base = os.environ.get("COACH_CONFIG_DIR")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "coach"


def _command_points_at_cli(cmd: str, plugin_root: str) -> bool:
    """A hook command is a CLI hook if it references one of our hook
    script names AND does NOT live under the plugin's own root.
    Plugin-self matches return False (those are our own hooks)."""
    if not any(name in cmd for name in HOOK_SCRIPT_NAMES):
        return False
    if plugin_root and plugin_root in cmd:
        return False
    return True


def _scan_hooks(data: dict, plugin_root: str) -> bool:
    """Walk settings.json hooks tree; return True if any non-plugin
    coach hook command is registered."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in ("SessionStart", "UserPromptSubmit"):
        groups = hooks.get(event) or []
        if not isinstance(groups, list):
            continue
        for grp in groups:
            if not isinstance(grp, dict):
                continue
            for h in (grp.get("hooks") or []):
                if not isinstance(h, dict):
                    continue
                cmd = str(h.get("command", ""))
                if _command_points_at_cli(cmd, plugin_root):
                    return True
    return False


def _write_defer_marker() -> None:
    """Persist a marker so /coach-claw:doctor can surface the deferral.
    Best-effort — never raises into the bootstrap path."""
    try:
        coach_dir = _coach_dir()
        coach_dir.mkdir(parents=True, exist_ok=True)
        marker = coach_dir / ".plugin-deferred"
        payload = {
            "deferred_at": datetime.now(timezone.utc).isoformat(),
            "reason": "cli-hooks-detected",
        }
        marker.write_text(json.dumps(payload))
    except Exception:
        pass


def main() -> int:
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    settings_path = Path(
        os.environ.get("CLAUDE_SETTINGS_PATH")
        or (Path.home() / ".claude" / "settings.json")
    )
    if not settings_path.exists():
        return 0
    try:
        data = json.loads(settings_path.read_text())
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    if _scan_hooks(data, plugin_root):
        _write_defer_marker()
        return DEFER_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
