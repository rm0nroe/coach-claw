"""Switch Coach control from the npm CLI to the Claude Code plugin.

Removes CLI-installed hook entries from ~/.claude/settings.json so the
plugin's hooks take over. Also clears a CLI-installed statusLine entry
if present (the plugin will reinstall its own on the next session via
statusline_self_patch).

Use case: a user has both distributions installed and the plugin's
coexistence_check is currently deferring to the CLI (.plugin-deferred
marker present). They want to flip control. Running
/coach-claw:switch is the supported way to do that without manually
editing settings.json.

The npm CLI's installed Python files (~/.claude/hooks/coach-*.py) are
NOT touched by this script — they remain on disk but are no longer
referenced from settings.json. Run `npx @rm0nroe/coach-claw uninstall`
separately for full CLI cleanup.

Atomic write under flock so concurrent /plugin install or other
settings mutations don't race.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coach_paths import resolve_coach_dir  # noqa: E402

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

HOOK_SCRIPT_NAMES = ("coach-session-start.py", "coach-user-prompt.py")
COACH_STATUSLINE_MARKERS = (
    "default-statusline-command.sh",
    "default_statusline.py",
)
# Substring fragments identifying the wrap-shape statusLine entries.
CLI_WRAP_TRAMPOLINE = "default-statusline-wrap-command.sh"
PLUGIN_WRAP_SCRIPT = "statusline_wrap.py"


def _is_cli_hook(cmd: str, plugin_root: str) -> bool:
    if not any(name in cmd for name in HOOK_SCRIPT_NAMES):
        return False
    if plugin_root and plugin_root in cmd:
        return False
    return True


def _is_cli_statusline(cmd: str, plugin_root: str) -> bool:
    """CLI's statusLine uses default-statusline-command.sh; plugin's
    uses default_statusline.py via bootstrap.sh. Removing the CLI's
    leaves the plugin's self-patch to reinstall on next SessionStart."""
    if "default-statusline-command.sh" not in cmd:
        return False
    if plugin_root and plugin_root in cmd:
        return False
    return True


def _strip_cli_hooks(data: dict, plugin_root: str) -> int:
    """Remove CLI-pattern hook entries in-place. Returns count removed."""
    removed = 0
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    for event in ("SessionStart", "UserPromptSubmit"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        new_groups = []
        for grp in groups:
            if not isinstance(grp, dict):
                new_groups.append(grp)
                continue
            inner = grp.get("hooks")
            if not isinstance(inner, list):
                new_groups.append(grp)
                continue
            new_inner = []
            for h in inner:
                if isinstance(h, dict) and _is_cli_hook(str(h.get("command", "")), plugin_root):
                    removed += 1
                    continue
                new_inner.append(h)
            if new_inner:
                new_groups.append({**grp, "hooks": new_inner})
            elif grp.get("hooks"):
                # Group emptied by removals — drop it entirely.
                pass
            else:
                new_groups.append(grp)
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]
    return removed


def _strip_cli_statusline(data: dict, plugin_root: str) -> bool:
    """Remove the CLI's default statusLine entry if present. Returns True
    if removed, False if nothing to do or it was someone else's."""
    sl = data.get("statusLine")
    if not isinstance(sl, dict):
        return False
    if _is_cli_statusline(str(sl.get("command", "")), plugin_root):
        del data["statusLine"]
        return True
    return False


def _rewrite_cli_wrap_to_plugin(data: dict, plugin_root: str) -> bool:
    """If the statusLine is the CLI wrap-shape trampoline AND we have a
    plugin_root to rewrite to, replace it with the plugin wrap shape
    (stable trampoline under coach_dir). Preserves
    `.statusline-wrap.json` (operate on settings.json only). Also
    writes the .plugin-root cache so the trampoline can resolve the
    active plugin path at exec time.

    Returns True when rewritten, False when nothing to do (no statusLine,
    not a wrap shape, or already plugin shape).
    """
    if not plugin_root:
        return False
    sl = data.get("statusLine")
    if not isinstance(sl, dict):
        return False
    cmd = str(sl.get("command", ""))
    # Skip if not the CLI wrap trampoline.
    if CLI_WRAP_TRAMPOLINE not in cmd:
        return False
    coach_dir = resolve_coach_dir()
    plugin_trampoline = coach_dir / "plugin-statusline.sh"
    # Already pointing at the plugin trampoline — leave alone.
    if str(plugin_trampoline) in cmd and PLUGIN_WRAP_SCRIPT in cmd:
        return False
    # Materialize the trampoline + .plugin-root cache so the new
    # command actually resolves on next render. Imported lazily so a
    # missing statusline_self_patch (unlikely) doesn't block hook strip.
    try:
        from statusline_self_patch import ensure_trampoline_installed  # noqa: WPS433
        ensure_trampoline_installed(coach_dir, Path(plugin_root))
    except Exception:
        pass
    quoted_trampoline = shlex.quote(str(plugin_trampoline))
    new_cmd = f"bash {quoted_trampoline} {PLUGIN_WRAP_SCRIPT}"
    data["statusLine"] = {"type": "command", "command": new_cmd}
    return True


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        fd, tmp = tempfile.mkstemp(
            prefix="." + path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise


def _write_marker(coach_dir: Path) -> None:
    """Persist a marker so `/coach-claw:doctor` and the deferred-state
    UI know the user explicitly switched."""
    try:
        coach_dir.mkdir(parents=True, exist_ok=True)
        marker = coach_dir / ".cli-uninstalled-by-plugin"
        marker.write_text(json.dumps({
            "switched_at": datetime.now(timezone.utc).isoformat(),
        }))
        # Clear any stale defer marker since CLI is gone now.
        defer = coach_dir / ".plugin-deferred"
        if defer.exists():
            defer.unlink()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Switch Coach control to the plugin.")
    parser.add_argument(
        "--settings",
        default=str(SETTINGS_PATH),
        help="Path to settings.json (default: ~/.claude/settings.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args(argv)

    settings_path = Path(args.settings)
    if not settings_path.exists():
        print(f"settings.json not found at {settings_path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(settings_path.read_text())
    except Exception as exc:
        print(f"settings.json is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("settings.json is not a JSON object", file=sys.stderr)
        return 2

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    removed_hooks = _strip_cli_hooks(data, plugin_root)
    rewritten_wrap = _rewrite_cli_wrap_to_plugin(data, plugin_root)
    # Wrap rewrite already mutated statusLine — don't also strip it.
    removed_statusline = (
        False if rewritten_wrap else _strip_cli_statusline(data, plugin_root)
    )

    if removed_hooks == 0 and not removed_statusline and not rewritten_wrap:
        print("No CLI hooks or statusLine found in settings.json — nothing to do.")
        return 0

    if args.dry_run:
        print(f"Would remove {removed_hooks} CLI hook entries.")
        if removed_statusline:
            print("Would remove CLI statusLine entry.")
        if rewritten_wrap:
            print("Would rewrite CLI wrap-statusLine to plugin wrap shape.")
        return 0

    _atomic_write(settings_path, data)
    _write_marker(resolve_coach_dir())

    print(f"Removed {removed_hooks} CLI hook entries.")
    if removed_statusline:
        print("Removed CLI statusLine entry. Plugin will reinstall its own next session.")
    if rewritten_wrap:
        print("Rewrote CLI wrap-statusLine to plugin wrap shape (saved original preserved).")
    print()
    print("Plugin is now in charge. The CLI's installed files in")
    print("  ~/.claude/hooks/coach-*.py  and  ~/.claude/coach/")
    print("are untouched. Run `npx @rm0nroe/coach-claw uninstall` for")
    print("full CLI removal (optional — coach state in ~/.claude/coach/")
    print("is shared between distributions and stays put either way).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
