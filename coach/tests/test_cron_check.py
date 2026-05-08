"""cron_check.is_cron_registered — best-effort detection of whether a
Coach insights cron/launchd plist is loaded.

Used by the plugin's UserPromptSubmit nudge block to decide whether to
suggest `npx @rm0nroe/coach-claw launchd`. Detection failures default
to True (assume registered) so users never get false-positive nudges.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import cron_check


# ---------------------------------------------------------------------------
# macOS path (launchctl)
# ---------------------------------------------------------------------------


def _mock_run(returncode: int, stdout: bytes = b"", stderr: bytes = b""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_macos_returns_true_when_launchctl_finds_plist(monkeypatch):
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Darwin")
    with patch.object(cron_check.subprocess, "run", return_value=_mock_run(0)):
        assert cron_check.is_cron_registered() is True


def test_macos_returns_false_when_launchctl_missing_plist(monkeypatch):
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Darwin")
    with patch.object(cron_check.subprocess, "run", return_value=_mock_run(113)):
        assert cron_check.is_cron_registered() is False


def test_macos_returns_true_on_subprocess_error(monkeypatch):
    """Fail-safe: launchctl missing or timing out → assume registered."""
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Darwin")
    with patch.object(
        cron_check.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=5),
    ):
        assert cron_check.is_cron_registered() is True


def test_macos_returns_true_when_launchctl_not_found(monkeypatch):
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Darwin")
    with patch.object(cron_check.subprocess, "run", side_effect=FileNotFoundError):
        assert cron_check.is_cron_registered() is True


# ---------------------------------------------------------------------------
# Linux path (crontab)
# ---------------------------------------------------------------------------


def test_linux_returns_false_when_crontab_empty(monkeypatch):
    """`crontab -l` returns nonzero when user has no crontab. That's
    the strongest signal Coach is not scheduled."""
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Linux")
    with patch.object(cron_check.subprocess, "run", return_value=_mock_run(1)):
        assert cron_check.is_cron_registered() is False


def test_linux_returns_true_when_coach_marker_in_crontab(monkeypatch):
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Linux")
    fake_crontab = b"0 4 * * * /home/u/.claude/coach/bin/insights.sh 1d\n"
    with patch.object(
        cron_check.subprocess, "run",
        return_value=_mock_run(0, stdout=fake_crontab),
    ):
        assert cron_check.is_cron_registered() is True


def test_linux_returns_false_when_crontab_has_only_other_jobs(monkeypatch):
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Linux")
    fake_crontab = b"0 5 * * * /usr/bin/something-else\n"
    with patch.object(
        cron_check.subprocess, "run",
        return_value=_mock_run(0, stdout=fake_crontab),
    ):
        assert cron_check.is_cron_registered() is False


def test_linux_recognizes_claude_coach_label(monkeypatch):
    """Alternate marker in case a future helper uses the `claude-coach`
    label instead of the script path."""
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Linux")
    fake_crontab = b"0 4 * * * /opt/claude-coach/run.sh\n"
    with patch.object(
        cron_check.subprocess, "run",
        return_value=_mock_run(0, stdout=fake_crontab),
    ):
        assert cron_check.is_cron_registered() is True


def test_linux_returns_true_on_subprocess_error(monkeypatch):
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Linux")
    with patch.object(
        cron_check.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="crontab", timeout=5),
    ):
        assert cron_check.is_cron_registered() is True


# ---------------------------------------------------------------------------
# Other platforms
# ---------------------------------------------------------------------------


def test_other_platforms_return_true(monkeypatch):
    """Windows / unknown systems don't use the cron path. Returning
    True suppresses the nudge."""
    monkeypatch.setattr(cron_check.platform, "system", lambda: "Windows")
    assert cron_check.is_cron_registered() is True
    monkeypatch.setattr(cron_check.platform, "system", lambda: "FreeBSD")
    assert cron_check.is_cron_registered() is True
