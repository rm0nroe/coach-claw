#!/usr/bin/env python3
"""Diagnostic surface for the Coach Claw plugin distribution.

Runs five read-only probes (plugin install, coexistence marker,
statusLine ownership, cron registration, venv health) and prints a
human-readable report by default. `--json` emits the same probe
results as machine-readable JSON for bug-report triage.

Also supports `--remove-statusline`: the documented uninstall-cleanup
path. Claude Code's plugin lifecycle does NOT clear the `statusLine`
key from `~/.claude/settings.json` when a plugin is uninstalled, so
this flag handles that one mutation. Only clears entries that match a
Coach marker; user-custom statusLines are left alone (reported as
"claimed" instead).

Reuses:
  - coach_paths.resolve_coach_dir for state-dir path
  - cron_check.is_cron_registered for the cron probe
  - statusline_self_patch.COACH_STATUSLINE_MARKERS for ownership matching
  - statusline_self_patch._atomic_write for the --remove-statusline path

Exit codes:
  0 — probes ran (the report itself may surface degraded states)
  1 — fatal error during probe rendering or settings.json mutation
  2 — invalid arguments
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coach_paths import resolve_coach_dir  # noqa: E402
from cron_check import is_cron_registered  # noqa: E402
from statusline_self_patch import (  # noqa: E402
    COACH_STATUSLINE_MARKERS,
    _atomic_write as _atomic_write_settings,
)
import statusline_wrap_action as wrap_action  # noqa: E402


SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
INSTALLED_PLUGINS_PATH = (
    Path.home() / ".claude" / "plugins" / "installed_plugins.json"
)

# Distinguish the two Coach statusLine shapes so the report can call
# them by name. These are substring matches on the full command string.
PLUGIN_STATUSLINE_MARKER = "default_statusline.py"
CLI_STATUSLINE_MARKER = "default-statusline-command.sh"

# ANSI palette — matches status.py's quiet, low-saturation style.
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[38;2;120;180;130m"
YELLOW = "\x1b[38;2;212;175;55m"
RED = "\x1b[38;2;200;110;100m"
GREY = "\x1b[38;2;110;122;140m"

OK = f"{GREEN}OK{RESET}"
WARN = f"{YELLOW}WARN{RESET}"
ERR = f"{RED}ERR{RESET}"
INFO = f"{GREY}--{RESET}"


# ---------------------------------------------------------------------------
# Probes — each returns a dict with at least a "status" key.
# ---------------------------------------------------------------------------

def probe_plugin_install() -> Dict[str, Any]:
    """Read installed_plugins.json; return any coach-claw entries."""
    if not INSTALLED_PLUGINS_PATH.exists():
        return {
            "status": "absent",
            "detail": "no installed_plugins.json (Claude Code plugin "
                      "system not initialized)",
            "entries": [],
        }
    try:
        data = json.loads(INSTALLED_PLUGINS_PATH.read_text())
    except Exception as e:
        return {
            "status": "error",
            "detail": f"failed to read installed_plugins.json: {e}",
            "entries": [],
        }
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return {
            "status": "error",
            "detail": "installed_plugins.json missing 'plugins' object",
            "entries": [],
        }

    entries = []
    for plugin_id, installs in plugins.items():
        if not plugin_id.startswith("coach-claw@"):
            continue
        if not isinstance(installs, list):
            continue
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            entries.append({
                "plugin_id": plugin_id,
                "marketplace": plugin_id.split("@", 1)[1] if "@" in plugin_id else "",
                "version": inst.get("version", ""),
                "install_path": inst.get("installPath", ""),
                "last_updated": inst.get("lastUpdated", ""),
                "scope": inst.get("scope", ""),
            })

    if not entries:
        return {
            "status": "absent",
            "detail": "no coach-claw entries in installed_plugins.json",
            "entries": [],
        }
    return {
        "status": "ok" if len(entries) == 1 else "warn",
        "detail": (
            "single install"
            if len(entries) == 1
            else f"{len(entries)} installs found (multiple marketplaces)"
        ),
        "entries": entries,
    }


def probe_coexistence(coach_dir: Path) -> Dict[str, Any]:
    """Look for the .plugin-deferred / .cli-uninstalled-by-plugin markers."""
    deferred = coach_dir / ".plugin-deferred"
    cli_removed = coach_dir / ".cli-uninstalled-by-plugin"

    deferred_payload = None
    if deferred.exists():
        try:
            deferred_payload = json.loads(deferred.read_text())
        except Exception:
            deferred_payload = {"raw": "<unparseable>"}

    cli_removed_payload = None
    if cli_removed.exists():
        try:
            cli_removed_payload = json.loads(cli_removed.read_text())
        except Exception:
            cli_removed_payload = {"raw": "<unparseable>"}

    if deferred_payload is not None:
        return {
            "status": "deferred",
            "detail": "plugin is yielding to the npm CLI hooks",
            "deferred_at": deferred_payload.get("deferred_at", ""),
            "reason": deferred_payload.get("reason", ""),
            "cli_removed_marker": cli_removed_payload,
        }
    return {
        "status": "active",
        "detail": "no .plugin-deferred marker; plugin's hooks fire normally",
        "deferred_at": "",
        "reason": "",
        "cli_removed_marker": cli_removed_payload,
    }


def _classify_statusline(entry: Any, coach_dir: Path | None = None) -> Dict[str, Any]:
    """Classify a settings.json statusLine value.

    Ownership states:
      absent                — no statusLine key
      ours-wrapped          — Coach's wrap shape (plugin or CLI trampoline)
      ours-plugin           — Coach's plugin default statusline
      ours-cli              — Coach's CLI default statusline
      ours-unknown          — Coach marker but unrecognized shape
      integrated-externally — non-Coach command BUT user's script already
                              calls Coach internals (sniffed via opt-out
                              marker reason `already-integrated`)
      claimed               — non-Coach command, no integration detected
    """
    if not isinstance(entry, dict):
        return {"ownership": "absent", "command": ""}
    cmd = str(entry.get("command", ""))
    if wrap_action._is_wrap_command(cmd):
        return {"ownership": "ours-wrapped", "command": cmd}
    if PLUGIN_STATUSLINE_MARKER in cmd:
        return {"ownership": "ours-plugin", "command": cmd}
    if CLI_STATUSLINE_MARKER in cmd:
        return {"ownership": "ours-cli", "command": cmd}
    if any(m in cmd for m in COACH_STATUSLINE_MARKERS):
        return {"ownership": "ours-unknown", "command": cmd}
    # Final check: did the manual-Coach pre-flight already mark this user
    # as integrated externally? The opt-out marker carries the reason.
    cdir = coach_dir if coach_dir is not None else resolve_coach_dir()
    disabled_path = cdir / wrap_action.DISABLED_MARKER_NAME
    try:
        if disabled_path.exists():
            data = json.loads(disabled_path.read_text())
            if isinstance(data, dict) and data.get("reason") == "already-integrated":
                return {
                    "ownership": "integrated-externally",
                    "command": cmd,
                    "detected_in": data.get("detected_in", ""),
                }
    except Exception:
        pass
    return {"ownership": "claimed", "command": cmd}


def probe_statusline(coach_dir: Path | None = None) -> Dict[str, Any]:
    """Read settings.json; classify the statusLine entry."""
    cdir = coach_dir if coach_dir is not None else resolve_coach_dir()
    if not SETTINGS_PATH.exists():
        return {
            "status": "absent",
            "detail": "no settings.json (Claude Code creates it on first run)",
            "ownership": "absent",
            "command": "",
        }
    try:
        data = json.loads(SETTINGS_PATH.read_text())
    except Exception as e:
        return {
            "status": "error",
            "detail": f"settings.json malformed: {e}",
            "ownership": "error",
            "command": "",
        }

    entry = data.get("statusLine") if isinstance(data, dict) else None
    classified = _classify_statusline(entry, coach_dir=cdir)
    ownership = classified["ownership"]

    result: Dict[str, Any] = {
        "ownership": ownership,
        "command": classified["command"],
    }

    if ownership == "absent":
        result["status"] = "absent"
        result["detail"] = "no statusLine key — plugin will install on next session"
    elif ownership == "ours-wrapped":
        result["status"] = "ok"
        result["detail"] = "statusLine wraps your existing command (Coach segment appended)"
        # Surface the saved original so the user can see what's underneath.
        try:
            marker = cdir / wrap_action.WRAP_MARKER_NAME
            if marker.exists():
                wrap_data = json.loads(marker.read_text())
                if isinstance(wrap_data, dict):
                    result["wrapped_original"] = wrap_data.get("original_command", "")
        except Exception:
            pass
    elif ownership in ("ours-plugin", "ours-cli", "ours-unknown"):
        result["status"] = "ok"
        result["detail"] = f"statusLine is ours ({ownership.split('-', 1)[1]})"
    elif ownership == "integrated-externally":
        result["status"] = "ok"
        result["detail"] = (
            "Custom statusline already integrates Coach internally — "
            "no wrap needed."
        )
        result["detected_in"] = classified.get("detected_in", "")
    else:
        result["status"] = "claimed"
        result["detail"] = "statusLine points elsewhere; can be wrapped"

    return result


def probe_cron() -> Dict[str, Any]:
    """Return whether a Coach insights cron is registered (best-effort)."""
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        return {
            "status": "skipped",
            "detail": f"{system}: no cron path on this platform",
            "registered": True,
            "platform": system,
        }
    try:
        registered = is_cron_registered()
    except Exception as e:
        return {
            "status": "error",
            "detail": f"cron probe failed: {e}",
            "registered": True,  # fail-safe (matches cron_check semantics)
            "platform": system,
        }
    if registered:
        return {
            "status": "ok",
            "detail": (
                "launchd plist com.local.claude-coach loaded"
                if system == "Darwin"
                else "crontab references coach insights script"
            ),
            "registered": True,
            "platform": system,
        }
    return {
        "status": "missing",
        "detail": (
            "no Coach launchd plist found — daily insights will not run.\n"
            "    Install via: npx @rm0nroe/coach-claw launchd"
            if system == "Darwin"
            else "no Coach line in crontab — daily insights will not run.\n"
            "    See README.md → Install → step 3 for the cron entry"
        ),
        "registered": False,
        "platform": system,
    }


def probe_venv() -> Dict[str, Any]:
    """Check that the plugin's PyYAML venv exists and imports yaml."""
    data_dir_env = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data_dir_env:
        venv_path = Path(data_dir_env) / "venv"
    else:
        venv_path = (
            Path.home() / ".claude" / "plugins" / "data" / "coach-claw" / "venv"
        )
    py = venv_path / "bin" / "python3"

    if not py.exists():
        return {
            "status": "missing",
            "detail": f"no python3 at {py} — bootstrap may have failed",
            "venv_path": str(venv_path),
            "yaml_version": "",
        }
    try:
        r = subprocess.run(
            [str(py), "-c", "import yaml; print(yaml.__version__)"],
            capture_output=True,
            timeout=5,
            text=True,
        )
    except Exception as e:
        return {
            "status": "error",
            "detail": f"venv python3 failed to launch: {e}",
            "venv_path": str(venv_path),
            "yaml_version": "",
        }
    if r.returncode != 0:
        return {
            "status": "broken",
            "detail": (
                "venv python3 exists but cannot import yaml "
                f"(stderr: {r.stderr.strip()[:120]})"
            ),
            "venv_path": str(venv_path),
            "yaml_version": "",
        }
    return {
        "status": "ok",
        "detail": f"PyYAML {r.stdout.strip()} importable from venv",
        "venv_path": str(venv_path),
        "yaml_version": r.stdout.strip(),
    }


# ---------------------------------------------------------------------------
# --remove-statusline action.
# ---------------------------------------------------------------------------

def remove_statusline() -> Dict[str, Any]:
    """Clear settings.json:statusLine if it currently points at Coach.

    Returns a result dict for the caller to render. Never raises into
    the SKILL surface — flock + atomic write semantics borrowed from
    statusline_self_patch._atomic_write.
    """
    if not SETTINGS_PATH.exists():
        return {
            "action": "remove-statusline",
            "result": "no-op",
            "detail": "no settings.json to modify",
        }
    try:
        data = json.loads(SETTINGS_PATH.read_text())
    except Exception as e:
        return {
            "action": "remove-statusline",
            "result": "error",
            "detail": f"settings.json malformed: {e}",
        }
    if not isinstance(data, dict):
        return {
            "action": "remove-statusline",
            "result": "error",
            "detail": "settings.json is not a JSON object",
        }

    entry = data.get("statusLine")
    classified = _classify_statusline(entry, coach_dir=resolve_coach_dir())
    ownership = classified["ownership"]

    if ownership == "absent":
        return {
            "action": "remove-statusline",
            "result": "no-op",
            "detail": "no statusLine key present",
        }
    # Protect non-Coach commands. `integrated-externally` is also non-
    # Coach (user's custom script that already calls Coach internals via
    # `coach/bin/stats.py` etc.) — deleting it would wipe their working
    # statusline. The ownership classifier marks both shapes as
    # leave-alone; --remove-statusline must respect that.
    if ownership in ("claimed", "integrated-externally"):
        return {
            "action": "remove-statusline",
            "result": "skipped",
            "detail": (
                "statusLine points at a non-Coach command; left untouched. "
                f"Command: {classified['command'][:120]}"
            ),
        }

    # ours-* — safe to clear.
    new_data = {k: v for k, v in data.items() if k != "statusLine"}
    try:
        _atomic_write_settings(SETTINGS_PATH, new_data)
    except Exception as e:
        return {
            "action": "remove-statusline",
            "result": "error",
            "detail": f"failed to write settings.json: {e}",
        }
    return {
        "action": "remove-statusline",
        "result": "removed",
        "detail": (
            "cleared the Coach statusLine entry. "
            "Run /plugin uninstall coach-claw to finish."
        ),
        "previous_command": classified["command"],
    }


def uninstall_prep(*, wipe_data: bool = False) -> Dict[str, Any]:
    """Pre-uninstall cleanup. Writes ~/.claude/coach/.uninstall-prepped
    marker so the UserPromptSubmit intercept lets the next
    /plugin uninstall through.

    Default: clears Coach statusLine, preserves profile data.
    With wipe_data=True: ALSO mv ~/.claude/coach/ → ~/.claude/coach.bak.<TS>/.
    Per CLAUDE.md "Never `rm -rf` anything. Use `mv` to a `.bak` path."
    """
    from datetime import datetime as _dt
    result: Dict[str, Any] = {
        "action": "uninstall-prep",
        "wipe_data": bool(wipe_data),
    }

    # Step 1: clear Coach statusLine (reuses remove_statusline for safety
    # contract — non-Coach entries stay untouched).
    rm = remove_statusline()
    result["statusline"] = rm

    coach_dir = resolve_coach_dir()
    archive_path: Path | None = None

    # Step 2 (optional): archive profile data.
    if wipe_data and coach_dir.exists():
        ts = _dt.utcnow().strftime("%Y%m%d-%H%M%S")
        archive_path = coach_dir.parent / f"coach.bak.{ts}"
        try:
            os.rename(coach_dir, archive_path)
        except OSError as e:
            return {
                **result,
                "result": "error",
                "detail": f"failed to archive coach dir: {e}",
            }
        # Recreate empty coach_dir so the marker we're about to write
        # has a parent. (Plugin's next SessionStart hook will repopulate
        # standard files on first run.)
        coach_dir.mkdir(parents=True, exist_ok=True)
        result["archived_to"] = str(archive_path)

    # Step 3: write the .uninstall-prepped marker. Acts as the bypass
    # gate for the UserPromptSubmit intercept on the next
    # /plugin uninstall attempt.
    marker = coach_dir / ".uninstall-prepped"
    try:
        coach_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({
                "prepped_at": _dt.utcnow().isoformat() + "Z",
                "wipe_data": bool(wipe_data),
                "archived_to": str(archive_path) if archive_path else None,
            })
        )
    except OSError as e:
        return {
            **result,
            "result": "error",
            "detail": f"failed to write marker: {e}",
        }

    result["result"] = "prepped"
    result["next_step"] = (
        "Run /plugin uninstall coach-claw@coach-claw-plugins to complete."
    )
    return result


# ---------------------------------------------------------------------------
# Output: human-readable report and JSON.
# ---------------------------------------------------------------------------

def _badge(status: str) -> str:
    return {
        "ok": OK,
        "active": OK,
        "absent": INFO,
        "skipped": INFO,
        "deferred": INFO,
        "claimed": WARN,
        "warn": WARN,
        "missing": WARN,
        "broken": ERR,
        "error": ERR,
    }.get(status, INFO)


def render_report(probes: Dict[str, Dict[str, Any]]) -> str:
    """Build the plain-text report. Sections are headed by 1) … 5)."""
    lines: list[str] = []
    lines.append(f"{BOLD}Coach Claw plugin diagnostic{RESET}")
    lines.append("")

    # 1) Plugin install
    p = probes["plugin_install"]
    lines.append(f"{_badge(p['status'])}  {BOLD}1) Plugin install{RESET}")
    if p["entries"]:
        for e in p["entries"]:
            lines.append(
                f"     {e['plugin_id']}  v{e['version']}  "
                f"({DIM}{e['scope']}{RESET})"
            )
            lines.append(f"     {DIM}{e['install_path']}{RESET}")
    else:
        lines.append(f"     {DIM}{p['detail']}{RESET}")
    lines.append("")

    # 2) Coexistence
    c = probes["coexistence"]
    lines.append(f"{_badge(c['status'])}  {BOLD}2) Coexistence{RESET}")
    lines.append(f"     {c['detail']}")
    if c["status"] == "deferred" and c.get("deferred_at"):
        lines.append(f"     {DIM}deferred_at: {c['deferred_at']}{RESET}")
    if c.get("cli_removed_marker"):
        lines.append(
            f"     {DIM}also: .cli-uninstalled-by-plugin marker present"
            f"{RESET}"
        )
    if c["status"] == "deferred":
        lines.append(f"     {DIM}▸ Coach IS running via the npm CLI — no action needed.{RESET}")
        lines.append(f"     {DIM}▸ /coach-claw:switch to flip control to the plugin instead.{RESET}")
    lines.append("")

    # 3) statusLine
    s = probes["statusline"]
    lines.append(f"{_badge(s['status'])}  {BOLD}3) statusLine ownership{RESET}")
    lines.append(f"     {s['detail']}")
    if s["ownership"] == "ours-wrapped" and s.get("wrapped_original"):
        lines.append(
            f"     {DIM}wrapped command: {s['wrapped_original'][:120]}{RESET}"
        )
        lines.append(f"     {DIM}wrapper:         {s['command'][:120]}{RESET}")
    elif s["ownership"] == "integrated-externally":
        if s.get("detected_in"):
            lines.append(f"     {DIM}detected in: {s['detected_in']}{RESET}")
        lines.append(f"     {DIM}command: {s['command'][:120]}{RESET}")
    elif s["command"]:
        lines.append(f"     {DIM}command: {s['command'][:120]}{RESET}")

    if s["ownership"] == "claimed":
        lines.append(
            f"     {DIM}▸ /coach-claw:doctor --wrap-statusline   "
            f"append Coach segment to it{RESET}"
        )
        lines.append(
            f"     {DIM}▸ Or replace settings.json statusLine.command with{RESET}"
        )
        lines.append(
            f"       {DIM}`bash ~/.claude/coach/default-statusline-command.sh`{RESET}"
        )
    elif s["ownership"] == "ours-wrapped":
        lines.append(
            f"     {DIM}▸ /coach-claw:doctor --unwrap-statusline   "
            f"restore the original{RESET}"
        )
    lines.append("")

    # 4) Cron
    cr = probes["cron"]
    lines.append(
        f"{_badge(cr['status'])}  {BOLD}4) Cron schedule{RESET}  "
        f"{DIM}({cr['platform']}){RESET}"
    )
    for line in cr["detail"].split("\n"):
        lines.append(f"     {line}")
    lines.append("")

    # 5) Venv
    v = probes["venv"]
    lines.append(f"{_badge(v['status'])}  {BOLD}5) Venv health{RESET}")
    lines.append(f"     {v['detail']}")
    lines.append(f"     {DIM}path: {v['venv_path']}{RESET}")
    lines.append("")

    return "\n".join(lines)


def collect_probes() -> Dict[str, Dict[str, Any]]:
    coach_dir = resolve_coach_dir()
    return {
        "plugin_install": probe_plugin_install(),
        "coexistence": probe_coexistence(coach_dir),
        "statusline": probe_statusline(coach_dir=coach_dir),
        "cron": probe_cron(),
        "venv": probe_venv(),
    }


# ---------------------------------------------------------------------------
# CLI entry.
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coach-claw doctor",
        description="Diagnose Coach Claw plugin state.",
    )
    parser.add_argument(
        "--remove-statusline",
        action="store_true",
        help="Clear the plugin's statusLine entry from settings.json "
             "(only if Coach-owned; user-custom entries are left alone).",
    )
    parser.add_argument(
        "--wrap-statusline",
        action="store_true",
        help="Wrap the user's existing statusLine so the Coach segment "
             "appends to it. Saves the original to "
             "~/.claude/coach/.statusline-wrap.json.",
    )
    parser.add_argument(
        "--unwrap-statusline",
        action="store_true",
        help="Restore the original statusLine and write a sticky opt-out "
             "marker so future auto-wrap attempts skip.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --wrap-statusline: clear opt-out marker and bypass the "
             "manual-Coach-integration pre-flight skip. With "
             "--unwrap-statusline: restore the saved original even if "
             "the current statusLine command was edited since wrap.",
    )
    parser.add_argument(
        "--prune-cache",
        action="store_true",
        help="Remove plugin cache dirs older than the active version. "
             "Combines with --dry-run to preview.",
    )
    parser.add_argument(
        "--uninstall-prep",
        action="store_true",
        help="Pre-uninstall cleanup. Removes plugin statusLine from "
             "settings.json + writes ~/.claude/coach/.uninstall-prepped "
             "marker so the UserPromptSubmit intercept lets the next "
             "/plugin uninstall through. Default preserves profile data; "
             "add --wipe-data to also archive it.",
    )
    parser.add_argument(
        "--wipe-data",
        action="store_true",
        help="With --uninstall-prep: also mv ~/.claude/coach/ to "
             "~/.claude/coach.bak.<TS>/. Reversible (not rm).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --prune-cache: show what would be removed without "
             "deleting.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit probe results as machine-readable JSON.",
    )
    args = parser.parse_args(argv)

    actions = sum([
        args.remove_statusline,
        args.wrap_statusline,
        args.unwrap_statusline,
        args.prune_cache,
        args.uninstall_prep,
    ])
    if actions > 1:
        sys.stderr.write(
            "doctor.py: --remove-statusline / --wrap-statusline / "
            "--unwrap-statusline are mutually exclusive\n"
        )
        return 2
    if actions and args.json:
        sys.stderr.write(
            "doctor.py: action flags cannot combine with --json\n"
        )
        return 2
    if args.force and not (args.wrap_statusline or args.unwrap_statusline):
        sys.stderr.write(
            "doctor.py: --force is only valid with --wrap-statusline or "
            "--unwrap-statusline\n"
        )
        return 2
    if args.wipe_data and not args.uninstall_prep:
        sys.stderr.write(
            "doctor.py: --wipe-data is only valid with --uninstall-prep\n"
        )
        return 2

    if args.remove_statusline:
        result = remove_statusline()
        print(json.dumps(result, indent=2))
        return 0 if result["result"] in ("removed", "no-op", "skipped") else 1

    if args.wrap_statusline:
        result = wrap_action.wrap(force=args.force)
        result["action"] = "wrap-statusline"
        print(json.dumps(result, indent=2))
        return 0 if result["result"] in ("wrapped", "no-op", "skipped") else 1

    if args.unwrap_statusline:
        result = wrap_action.unwrap(force=args.force)
        result["action"] = "unwrap-statusline"
        print(json.dumps(result, indent=2))
        return 0 if result["result"] in ("unwrapped", "no-op", "refused") else 1

    if args.prune_cache:
        import cache_prune
        removed = cache_prune.prune_inactive_cache_versions(
            dry_run=args.dry_run, verbose=True
        )
        verb = "would-remove" if args.dry_run else "removed"
        result = {
            "action": "prune-cache",
            "result": verb,
            "count": len(removed),
            "paths": [str(p) for p in removed],
        }
        print(json.dumps(result, indent=2))
        return 0

    if args.uninstall_prep:
        result = uninstall_prep(wipe_data=args.wipe_data)
        print(json.dumps(result, indent=2))
        return 0 if result["result"] in ("prepped", "no-op") else 1

    probes = collect_probes()

    if args.json:
        print(json.dumps(probes, indent=2))
        return 0

    print(render_report(probes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
