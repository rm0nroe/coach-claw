"""statusline_self_patch.ensure_statusline_installed — plugin self-patch
of ~/.claude/settings.json:statusLine.

Plugin distribution only. Gating happens at the call site
(coach-session-start.py:_maybe_install_plugin_statusline checks
CLAUDE_PLUGIN_ROOT). These tests exercise the patcher directly with a
tmpdir settings.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import statusline_self_patch as sp


@pytest.fixture
def settings(tmp_path):
    """Return a settings.json path inside tmp_path. Caller writes it."""
    return tmp_path / "settings.json"


@pytest.fixture
def plugin_root(tmp_path):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    return root


def test_inserts_statusline_when_absent(settings, plugin_root):
    settings.write_text(json.dumps({}))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "installed"

    written = json.loads(settings.read_text())
    assert "statusLine" in written
    cmd = written["statusLine"]["command"]
    assert "bootstrap.sh" in cmd
    assert "default_statusline.py" in cmd
    # Absolute path (no ${CLAUDE_PLUGIN_ROOT} placeholder; that wouldn't
    # expand inside settings.json).
    assert "${CLAUDE_PLUGIN_ROOT}" not in cmd


def test_preserves_other_keys(settings, plugin_root):
    """Patching statusLine must not clobber unrelated settings."""
    settings.write_text(json.dumps({
        "permissions": {"allow": ["read"]},
        "hooks": {"SessionStart": [{"hooks": [{"command": "foo"}]}]},
    }))
    sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    written = json.loads(settings.read_text())
    assert written["permissions"] == {"allow": ["read"]}
    assert "SessionStart" in written["hooks"]


def test_noop_when_coach_statusline_already_present(settings, plugin_root):
    """statusLine pointing at Coach's known marker → no-op (file
    untouched)."""
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash /some/other/path/default-statusline-command.sh",
        },
    }))
    mtime_before = settings.stat().st_mtime_ns
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "matched"
    assert settings.stat().st_mtime_ns == mtime_before, (
        "matched path must not rewrite the file"
    )


def test_noop_when_other_statusline_already_present(settings, plugin_root, capfd):
    """statusLine pointing at someone else's command → no-op + stderr
    note. We do not overwrite user-customized or third-party statusLine
    entries — that would be hostile."""
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash /custom/user-thing.sh"},
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "claimed"
    written = json.loads(settings.read_text())
    assert written["statusLine"]["command"] == "bash /custom/user-thing.sh"
    err = capfd.readouterr().err
    assert "coach-claw" in err.lower() or "statusline" in err.lower()


def test_skipped_when_settings_absent(tmp_path, plugin_root):
    """No settings.json → no-op (returns 'skipped'). Don't create it
    from scratch — Claude Code itself will create it on first run."""
    missing = tmp_path / "no-such-settings.json"
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=missing)
    assert result == "skipped"
    assert not missing.exists()


def test_error_on_malformed_json(settings, plugin_root):
    """Existing settings.json that's not valid JSON → returns 'error',
    never raises (caller is hook context — mustn't crash)."""
    settings.write_text("{ not valid json")
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "error"


def test_atomic_no_partial_write_on_failure(settings, plugin_root, monkeypatch):
    """If json.dump raises mid-write, settings.json must not be
    truncated. Verified by simulating an exception in os.replace."""
    settings.write_text(json.dumps({"statusLine": None}))
    original = json.loads(settings.read_text())

    real_replace = sp.os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(sp.os, "replace", boom)
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "error"
    # Original file content preserved (atomic semantics).
    after = json.loads(settings.read_text())
    assert after == original


def test_recognizes_cli_installed_statusline(settings, plugin_root):
    """A CLI-installed statusLine (uses default-statusline-command.sh)
    must be recognized as 'ours' so the plugin doesn't try to
    overwrite when the user has both installs side-by-side."""
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash /Users/foo/.claude/coach/default-statusline-command.sh",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "matched"


def test_recognizes_plugin_installed_statusline(settings, plugin_root):
    """Plugin-installed statusLine (uses default_statusline.py via
    bootstrap.sh) must also be recognized as 'ours' on the second
    SessionStart."""
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "/path/to/plugin/bin/bootstrap.sh /path/to/plugin/bin/default_statusline.py",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "matched"
