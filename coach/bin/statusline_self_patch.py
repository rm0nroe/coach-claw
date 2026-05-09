"""Idempotently install Coach's main statusLine into ~/.claude/settings.json.

Plugin distribution only — Claude Code's plugin model doesn't expose the
top-level `statusLine` key in plugin settings.json (only `agent` and
`subagentStatusLine` are supported per plugins-reference). Workaround:
the plugin's SessionStart hook calls `ensure_statusline_installed()` on
every session start; first call writes the entry, subsequent calls are
O(read).

Why a self-patching approach (vs a one-time companion CLI):
  - Self-healing on plugin install / version-bump.
  - No extra setup step — `/plugin install coach-claw` and the
    statusline appears next session.
  - The cost — `/plugin uninstall coach-claw` does NOT auto-clean the
    settings.json key (Anthropic's plugin lifecycle doesn't touch
    settings.json keys plugins added). `/coach-claw:doctor
    --remove-statusline` covers that path.

Decisions:
  - `${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside settings.json (only
    in plugin's own files). Resolve absolute path at write time.
  - If a `statusLine` already exists pointing at Coach's known marker
    paths, no-op.
  - If a `statusLine` already exists pointing somewhere else (user's
    custom one, another plugin's), DO NOT overwrite. Log via stderr.
  - Atomic write under flock (mirrors merge.py:atomic_write_yaml).
  - CLI distribution: this module is in coach/bin/ for code-share, but
    the CLI's coach-session-start.py never calls into it (CLI manages
    its own statusLine via install.sh). Gating happens at the call
    site via `os.environ.get("CLAUDE_PLUGIN_ROOT")`.
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Optional


SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Substring markers that identify a statusLine command as Coach's. The
# default-shape markers (CLI install via default-statusline-command.sh,
# plugin via default_statusline.py through bootstrap.sh) and the wrap-
# shape markers (statusline_wrap.py for plugin, default-statusline-wrap-
# command.sh for CLI) are all recognized, so a user with any combination
# installed never gets a duplicate or overwrite.
COACH_STATUSLINE_MARKERS = (
    "default-statusline-command.sh",
    "default_statusline.py",
    "statusline_wrap.py",
    "default-statusline-wrap-command.sh",
)

# Subset of the markers above that identify the WRAP shape specifically.
# Lets `ensure_statusline_installed` route ours-wrapped through a path-
# freshness refresh instead of the simple matched no-op.
_WRAP_STATUSLINE_MARKERS = (
    "statusline_wrap.py",
    "default-statusline-wrap-command.sh",
)


def _is_coach_statusline(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    cmd = str(entry.get("command", ""))
    return any(m in cmd for m in COACH_STATUSLINE_MARKERS)


def _is_wrap_statusline(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    cmd = str(entry.get("command", ""))
    return any(m in cmd for m in _WRAP_STATUSLINE_MARKERS)


def _desired_entry(plugin_root: Path) -> dict:
    """Build the statusLine entry for the plugin distribution.

    Uses bootstrap.sh as the wrapper so PyYAML imports inside
    default_statusline.py succeed (the script imports stats.py which
    imports user_config.py which doesn't need yaml directly, but
    keeping a single entry pattern simplifies the model).
    """
    bootstrap = shlex.quote(str(plugin_root / "bin" / "bootstrap.sh"))
    statusline_py = shlex.quote(str(plugin_root / "bin" / "default_statusline.py"))
    return {
        "type": "command",
        "command": f"{bootstrap} {statusline_py}",
    }


def _atomic_write(path: Path, payload: dict) -> None:
    """tempfile + os.replace under flock — same pattern as merge.py."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass  # flock unsupported — proceed best-effort
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


def ensure_statusline_installed(
    plugin_root: str,
    settings_path: Optional[Path] = None,
) -> str:
    """Idempotently set Coach's statusLine in `settings_path`.

    Returns one of:
        "installed"      — wrote a new entry (was absent).
        "matched"        — already present and ours; no write.
        "wrap-refreshed" — ours-wrapped with stale plugin path; rewrote
                           with current ${CLAUDE_PLUGIN_ROOT}.
        "wrapped"        — auto-wrapped a user's claimed statusline so
                           the Coach segment now appends to it.
        "claimed"        — present pointing at someone else AND auto-
                           wrap was suppressed (opt-out marker or manual
                           Coach integration detected).
        "skipped"        — settings.json doesn't exist (very fresh user;
                           Claude Code creates it on first run).
        "error"          — exception during read/write (always fail-soft).

    `settings_path` defaults to ~/.claude/settings.json; tests pass a
    tmpdir path.
    """
    target = settings_path or SETTINGS_PATH
    try:
        if not target.exists():
            return "skipped"

        try:
            data = json.loads(target.read_text())
        except json.JSONDecodeError:
            return "error"
        if not isinstance(data, dict):
            return "error"

        plugin_root_p = Path(plugin_root).resolve()
        existing = data.get("statusLine")
        if existing:
            # Wrap shape — delegate to the action module so path-freshness
            # refresh and idempotency live in one place.
            if _is_wrap_statusline(existing):
                try:
                    import statusline_wrap_action as wa  # noqa: WPS433
                    res = wa.wrap(
                        settings_path=target,
                        plugin_root=plugin_root_p,
                    )
                except Exception:
                    return "matched"  # fail-soft; no rewrite happened
                if res.get("reason") == "refreshed-path":
                    return "wrap-refreshed"
                return "matched"
            if _is_coach_statusline(existing):
                return "matched"

            # Claimed: auto-wrap on first encounter, unless opted out or
            # manual-Coach integration detected. wrap_action.wrap()
            # handles both gates internally and falls through to a wrap
            # mutation on success.
            try:
                import statusline_wrap_action as wa  # noqa: WPS433
                res = wa.wrap(
                    settings_path=target,
                    plugin_root=plugin_root_p,
                )
            except Exception:
                res = {"result": "error"}
            if res.get("result") == "wrapped":
                return "wrapped"
            sys.stderr.write(
                "coach-claw plugin: settings.json statusLine left "
                "untouched. Run /coach-claw:doctor to inspect.\n"
            )
            return "claimed"

        data["statusLine"] = _desired_entry(plugin_root_p)
        _atomic_write(target, data)
        return "installed"
    except Exception:
        return "error"
