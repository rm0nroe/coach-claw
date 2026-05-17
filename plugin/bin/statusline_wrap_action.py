#!/usr/bin/env python3
"""wrap / unwrap actions for the user's statusLine command.

Shared by `coach/bin/doctor.py`, the plugin's SessionStart hook
(`statusline_self_patch.py`), and the CLI's `install.sh`. All three
call into the same code so behavior stays consistent across surfaces.

The module exposes two main functions:

  wrap(*, coach_dir, settings_path, plugin_root, force) -> dict
  unwrap(*, coach_dir, settings_path, force, mark_opted_out) -> dict

…and one CLI subcommand for shell callers (install.sh, hooks):

  python3 statusline_wrap_action.py wrap-if-claimed
        Auto-wrap on first run when settings.json:statusLine is
        `claimed` and no opt-out marker exists. Honors
        $CLAUDE_PLUGIN_ROOT (plugin shape) when set, falls through to
        CLI shape otherwise. Always exits 0 (never breaks an install).

Markers (all under `<coach_dir>/`):

  .statusline-wrap.json              saved original; written by wrap;
                                     deleted by unwrap.
  .statusline-wrap-disabled          sticky opt-out; written by unwrap
                                     or by the manual-Coach skip path;
                                     cleared by an explicit wrap.
  .statusline-wrap-announced         one-time banner trigger; written by
                                     wrap; consumed by the user-prompt hook.
  .statusline-wrap-duplicate-detected runtime detection trigger; written by
                                     statusline_wrap.py at render time when
                                     it spots a Coach signature in the
                                     original output.

Path resolution (fix #3): every public function takes explicit
`coach_dir` + `settings_path` kwargs. They default to env-aware
resolution (`coach_paths.resolve_coach_dir()` for coach_dir,
`$CLAUDE_SETTINGS_PATH` or `~/.claude/settings.json` for settings).
Tests always pass explicit paths.

Wrapper command shapes (fix #2):
  - CLI:    `bash <coach_dir>/default-statusline-wrap-command.sh`
  - Plugin: `<plugin_root>/bin/bootstrap.sh <plugin_root>/bin/statusline_wrap.py`
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coach_paths import resolve_coach_dir  # noqa: E402
from statusline_self_patch import (  # noqa: E402
    COACH_STATUSLINE_MARKERS,
    _atomic_write,
    ensure_trampoline_installed,
)

# ---------------------------------------------------------------------------
# Marker filenames — single source of truth so doctor + hook + tests agree.
# ---------------------------------------------------------------------------

WRAP_MARKER_NAME = ".statusline-wrap.json"
DISABLED_MARKER_NAME = ".statusline-wrap-disabled"
ANNOUNCE_MARKER_NAME = ".statusline-wrap-announced"
DUPLICATE_MARKER_NAME = ".statusline-wrap-duplicate-detected"

# Substring fragments that identify our wrapper command in a
# settings.json statusLine entry. The plugin shape contains
# `statusline_wrap.py` directly; the CLI shape goes through a shell
# trampoline whose name contains `default-statusline-wrap-command.sh`.
# Either match means "this statusLine is currently our wrapper".
WRAP_SCRIPT_MARKER = "statusline_wrap.py"
WRAP_TRAMPOLINE_MARKER = "default-statusline-wrap-command.sh"
WRAP_COMMAND_MARKERS = (WRAP_SCRIPT_MARKER, WRAP_TRAMPOLINE_MARKER)


def _is_wrap_command(cmd: str) -> bool:
    return any(m in cmd for m in WRAP_COMMAND_MARKERS)

# Regex pulled from the existing patcher — recognizes Coach references in
# a user's custom shell statusline (default integration via stats.py /
# default_statusline.py / etc.). Anchors the manual-Coach pre-flight.
_INTEGRATION_REFS = (
    "coach/bin/stats.py",
    "default_statusline.py",
    "coach/bin/",
    "default-statusline-command.sh",
    "default_statusline_path",
)


# ---------------------------------------------------------------------------
# Path + marker helpers
# ---------------------------------------------------------------------------


def _default_settings_path() -> Path:
    env = os.environ.get("CLAUDE_SETTINGS_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "settings.json"


def _resolve_dir(coach_dir: Path | None) -> Path:
    return coach_dir if coach_dir is not None else resolve_coach_dir()


def _resolve_settings(settings_path: Path | None) -> Path:
    return settings_path if settings_path is not None else _default_settings_path()


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Classification (matches doctor / statusline_self_patch logic).
# ---------------------------------------------------------------------------


def _classify(entry, coach_dir: Path) -> str:
    """One of: absent, ours-default, ours-wrapped, integrated-externally, claimed."""
    if not isinstance(entry, dict):
        return "absent"
    cmd = str(entry.get("command", ""))
    if not cmd:
        return "absent"
    if _is_wrap_command(cmd):
        return "ours-wrapped"
    if any(m in cmd for m in COACH_STATUSLINE_MARKERS):
        return "ours-default"
    disabled = _read_json(coach_dir / DISABLED_MARKER_NAME) or {}
    if disabled.get("reason") == "already-integrated":
        return "integrated-externally"
    return "claimed"


# ---------------------------------------------------------------------------
# Manual-Coach pre-flight: sniff the user's script for Coach references.
# ---------------------------------------------------------------------------


_SCRIPT_PATH_RE = re.compile(r"(?:^|\s)((?:/|~|\$HOME|\$\{HOME\})\S+\.sh)")


def _resolve_script_path(command: str) -> Path | None:
    """Best-effort: locate the script file referenced by `command`.

    Handles:
      - `bash /abs/path/to/script.sh`
      - `/abs/path/to/script.sh`
      - `bash ~/path/to/script.sh` (expanduser)
      - `bash $HOME/path/...` (env-expand)

    Returns None if no script-file is identifiable (e.g., `bash -c "..."`,
    inline pipelines), in which case the static sniff is skipped.
    """
    if not command:
        return None
    expanded = os.path.expanduser(os.path.expandvars(command))
    # Tokenize like a shell does; first .sh / .py path token wins.
    try:
        parts = shlex.split(expanded, comments=False, posix=True)
    except ValueError:
        return None
    for tok in parts:
        # Skip bash flags and `-c` (signals an inline script body).
        if tok.startswith("-"):
            return None
        if tok.endswith(".sh") or tok.endswith(".py"):
            p = Path(tok)
            if p.is_absolute() and p.exists():
                return p
            return None
    return None


def _detect_manual_coach_integration(command: str) -> tuple[bool, Path | None]:
    """If the user's statusLine script references Coach internals, return
    (True, path). Else (False, None)."""
    script = _resolve_script_path(command)
    if script is None:
        return False, None
    try:
        content = script.read_text(errors="replace")
    except Exception:
        return False, None
    if any(ref in content for ref in _INTEGRATION_REFS):
        return True, script
    return False, None


# ---------------------------------------------------------------------------
# Wrapper-command builders.
# ---------------------------------------------------------------------------


def _build_wrapper_command(
    *, coach_dir: Path, plugin_root: Path | None
) -> str:
    """Return the settings.json `command` string for the wrap shape.

    Plugin path-shape routes through the stable trampoline under
    `coach_dir/plugin-statusline.sh` (written by
    `statusline_self_patch.ensure_trampoline_installed`). The trampoline
    resolves the active plugin install path at exec time from a small
    cache file, so settings.json never embeds a versioned plugin path
    that goes stale across `/plugin update` cycles. CLI uses a shell
    trampoline at `<coach_dir>/default-statusline-wrap-command.sh` so
    the path stays stable across coach upgrades there too.
    """
    if plugin_root is not None:
        trampoline = shlex.quote(str(coach_dir / "plugin-statusline.sh"))
        return f"bash {trampoline} statusline_wrap.py"
    trampoline = shlex.quote(str(coach_dir / "default-statusline-wrap-command.sh"))
    return f"bash {trampoline}"


# ---------------------------------------------------------------------------
# Public API: wrap / unwrap
# ---------------------------------------------------------------------------


def wrap(
    *,
    coach_dir: Path | None = None,
    settings_path: Path | None = None,
    plugin_root: Path | None = None,
    force: bool = False,
) -> dict:
    """Idempotent wrap. Returns a result dict.

    Result `result` field:
      "wrapped"             — settings.json mutated, original saved.
      "skipped"             — no mutation; reason in `reason` field.
      "no-op"               — already in target state.
      "error"               — read/write failure (always fail-soft).

    Detailed `reason` strings (when `result == "skipped"`):
      "absent"              — settings.json has no statusLine entry at all.
      "already-coach"       — statusLine is already Coach (default or wrapped).
      "opted-out"           — opt-out marker present (cleared by force=True).
      "already-integrated"  — manual-Coach pre-flight detected refs.

    `force=True` clears the opt-out marker AND bypasses the manual-Coach
    pre-flight skip. It does NOT bypass `already-coach` (idempotent
    no-op stays a no-op).
    """
    cdir = _resolve_dir(coach_dir)
    spath = _resolve_settings(settings_path)
    cdir.mkdir(parents=True, exist_ok=True)

    # Plugin context: refresh the trampoline + .plugin-root cache so the
    # stable path produced by _build_wrapper_command always resolves to
    # the active plugin install. No-op for CLI calls (plugin_root is None).
    if plugin_root is not None:
        try:
            ensure_trampoline_installed(cdir, plugin_root)
        except Exception:
            pass

    try:
        settings = _read_json(spath)
        if settings is None:
            return {"result": "skipped", "reason": "no-settings"}

        entry = settings.get("statusLine")
        kind = _classify(entry, cdir)
        if kind == "absent":
            return {"result": "skipped", "reason": "absent"}
        if kind in ("ours-default", "ours-wrapped"):
            # Special case: ours-wrapped with stale wrapper path → refresh.
            if kind == "ours-wrapped":
                desired = _build_wrapper_command(
                    coach_dir=cdir, plugin_root=plugin_root
                )
                current = str((entry or {}).get("command", ""))
                if current != desired:
                    settings["statusLine"] = {
                        "type": "command",
                        "command": desired,
                    }
                    _atomic_write(spath, settings)
                    return {"result": "wrapped", "reason": "refreshed-path",
                            "command": desired}
                return {"result": "no-op", "reason": "already-wrapped"}
            return {"result": "no-op", "reason": "already-coach"}

        # Opt-out gate (clearable via force=True).
        disabled_path = cdir / DISABLED_MARKER_NAME
        if disabled_path.exists() and not force:
            return {"result": "skipped", "reason": "opted-out"}

        original_cmd = str((entry or {}).get("command", ""))

        # Manual-Coach pre-flight: skip + write opt-out marker.
        manual, script = _detect_manual_coach_integration(original_cmd)
        if manual and not force:
            _write_json(disabled_path, {
                "reason": "already-integrated",
                "detected_in": str(script) if script else None,
                "detected_at": _utc_now_iso(),
            })
            return {"result": "skipped", "reason": "already-integrated",
                    "detected_in": str(script) if script else None}

        # Save original BEFORE mutating settings.json.
        wrap_payload = {
            "original_command": original_cmd,
            "wrapped_at": _utc_now_iso(),
        }
        _write_json(cdir / WRAP_MARKER_NAME, wrap_payload)

        # Mutate settings.json atomically.
        wrapper_cmd = _build_wrapper_command(
            coach_dir=cdir, plugin_root=plugin_root
        )
        settings["statusLine"] = {"type": "command", "command": wrapper_cmd}
        _atomic_write(spath, settings)

        # Clear opt-out (re-opt-in semantics) and set the announce marker.
        if disabled_path.exists():
            try:
                disabled_path.unlink()
            except Exception:
                pass
        _write_json(cdir / ANNOUNCE_MARKER_NAME, {
            # `created_at` is the field _read_and_consume reads for TTL —
            # stay aligned with the rest of the marker ecosystem.
            "created_at": _utc_now_iso(),
            "consumed_by": [],
        })

        return {"result": "wrapped", "command": wrapper_cmd,
                "saved_original": original_cmd}
    except Exception as exc:
        return {"result": "error", "reason": "exception", "detail": str(exc)}


def unwrap(
    *,
    coach_dir: Path | None = None,
    settings_path: Path | None = None,
    force: bool = False,
    mark_opted_out: bool = True,
) -> dict:
    """Restore the saved original command. Sticky opt-out by default.

    Result `result` field:
      "unwrapped"  — restored.
      "no-op"      — nothing to do (no marker / no settings).
      "refused"    — current command was edited since wrap; refusing
                     unless `force=True`.
      "error"      — read/write failure.
    """
    cdir = _resolve_dir(coach_dir)
    spath = _resolve_settings(settings_path)

    try:
        marker = cdir / WRAP_MARKER_NAME
        wrap_data = _read_json(marker)
        if wrap_data is None:
            return {"result": "no-op", "reason": "no-wrap-marker"}

        saved_original = wrap_data.get("original_command")
        if not isinstance(saved_original, str):
            return {"result": "error", "reason": "marker-invalid"}

        settings = _read_json(spath)
        if settings is None:
            return {"result": "no-op", "reason": "no-settings"}

        current_cmd = str(((settings.get("statusLine") or {}) or {}).get("command", ""))
        if not _is_wrap_command(current_cmd) and not force:
            return {
                "result": "refused",
                "reason": "command-changed-since-wrap",
                "current_command": current_cmd,
                "saved_original": saved_original,
            }

        settings["statusLine"] = {"type": "command", "command": saved_original}
        _atomic_write(spath, settings)

        try:
            marker.unlink()
        except Exception:
            pass

        if mark_opted_out:
            _write_json(cdir / DISABLED_MARKER_NAME, {
                "reason": "user-unwrapped",
                "unwrapped_at": _utc_now_iso(),
            })

        return {"result": "unwrapped", "restored_command": saved_original}
    except Exception as exc:
        return {"result": "error", "reason": "exception", "detail": str(exc)}


# ---------------------------------------------------------------------------
# CLI: `wrap-if-claimed`
# ---------------------------------------------------------------------------


def _cmd_wrap_if_claimed(argv: list[str]) -> int:
    plugin_root_env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    plugin_root = Path(plugin_root_env).resolve() if plugin_root_env else None
    result = wrap(plugin_root=plugin_root)
    code = result.get("result", "error")
    reason = result.get("reason", "")
    if code == "wrapped":
        print(f"  OK: wrapped existing statusLine (Coach segment will append)")
    elif code == "skipped" and reason == "already-integrated":
        path = result.get("detected_in", "")
        suffix = f" (detected in {path})" if path else ""
        print(f"  OK: leaving statusLine alone — already integrates Coach{suffix}")
    elif code == "skipped" and reason == "opted-out":
        print(f"  OK: leaving statusLine alone — user opted out via --unwrap-statusline")
    elif code == "skipped" and reason == "absent":
        print(f"  note: no statusLine to wrap — nothing to do")
    elif code == "no-op":
        print(f"  OK: statusLine already wrapped (no change)")
    elif code == "error":
        print(f"  warn: wrap-if-claimed failed: {result.get('detail', '')}")
    else:
        print(f"  note: wrap-if-claimed result={code} reason={reason}")
    return 0  # never break an install


def _build_parser():
    import argparse
    p = argparse.ArgumentParser(prog="statusline_wrap_action")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("wrap-if-claimed",
                   help="Auto-wrap when statusLine is claimed; else no-op.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "wrap-if-claimed":
        return _cmd_wrap_if_claimed(argv or [])
    return 0


if __name__ == "__main__":
    sys.exit(main())
