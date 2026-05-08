"""Detect whether the daily Coach insights cron is registered.

Plugin distribution context only — the npm CLI's `coach-claw launchd`
subcommand is the canonical way to register OS-level scheduling. The
plugin model has no equivalent (monitors are event-streaming, not
cron-like). When a user installs only the plugin, profile.yaml never
gets the daily deterministic refresh and Coach silently grows stale.

This module powers a one-time nudge banner: detect the gap, suggest
the CLI command, write a marker so we don't re-nudge.

Detection is best-effort and fail-safe — if launchctl/crontab errors
or times out, we return True (assume registered) so we never harass a
user with false-positive nudges.
"""
from __future__ import annotations

import platform
import subprocess


# Default plist label registered by install-launchd.sh.
LAUNCHD_LABEL = "com.local.claude-coach"

# Substrings that indicate a Coach cron line on Linux. Matches both the
# canonical `~/.claude/coach/bin/insights.sh 1d` pattern and the
# possibly-renamed `claude-coach` script if a future helper introduces
# one.
LINUX_CRON_MARKERS = ("claude-coach", "coach/bin/insights.sh")


def is_cron_registered() -> bool:
    """Return True if a Coach insights cron is already registered.

    macOS: queries `launchctl list <LAUNCHD_LABEL>`. Exit 0 means the
    plist is loaded.

    Linux: greps `crontab -l` for known Coach markers. Empty crontab
    or grep miss → False.

    Other platforms: returns True (no-op — Windows and other systems
    don't use the cron path; we don't want to nudge).

    Errors during detection (timeouts, missing binaries) → True. Better
    to suppress a nudge than to fire a false-positive.
    """
    system = platform.system()
    if system == "Darwin":
        return _launchctl_loaded(LAUNCHD_LABEL)
    if system == "Linux":
        return _crontab_has_coach()
    return True


def _launchctl_loaded(label: str) -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return True  # fail-safe: don't nudge on detection error


def _crontab_has_coach() -> bool:
    try:
        r = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            timeout=5,
        )
        # `crontab -l` exits nonzero when the user has no crontab at all.
        # That's the strongest "not registered" signal we get.
        if r.returncode != 0:
            return False
        out = r.stdout.decode("utf-8", errors="replace")
        return any(marker in out for marker in LINUX_CRON_MARKERS)
    except Exception:
        return True
