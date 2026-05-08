"""switch_to_plugin.py — flip Coach control from npm CLI to plugin.

Pairs with /coach-claw:switch skill. Removes CLI-installed hook entries
from settings.json, optionally clears the CLI's statusLine, writes a
marker so /coach-claw:doctor knows the user explicitly switched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import switch_to_plugin as sp


def _settings_with_cli_hooks_and_statusline() -> dict:
    return {
        "permissions": {"allow": ["read"]},
        "hooks": {
            "SessionStart": [{"hooks": [{
                "type": "command",
                "command": "/usr/bin/python3 /Users/foo/.claude/hooks/coach-session-start.py",
                "timeout": 3,
            }]}],
            "UserPromptSubmit": [{"hooks": [{
                "type": "command",
                "command": "/usr/bin/python3 /Users/foo/.claude/hooks/coach-user-prompt.py",
                "timeout": 2,
            }]}],
        },
        "statusLine": {
            "type": "command",
            "command": "bash /Users/foo/.claude/coach/default-statusline-command.sh",
        },
    }


def _settings_with_plugin_hooks(plugin_root: str) -> dict:
    cmd = f"{plugin_root}/bin/bootstrap.sh {plugin_root}/hooks/coach-session-start.py"
    return {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}],
        }
    }


@pytest.fixture
def env_under_plugin(tmp_path, monkeypatch):
    """Set up CLAUDE_PLUGIN_ROOT and a coach state dir."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    coach_dir = tmp_path / "coach"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    return plugin_root, coach_dir


def test_strips_cli_hooks_and_statusline(tmp_path, env_under_plugin, capsys):
    plugin_root, coach_dir = env_under_plugin
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_cli_hooks_and_statusline()))

    rc = sp.main(["--settings", str(settings)])
    assert rc == 0

    after = json.loads(settings.read_text())
    # Hook entries gone
    assert "SessionStart" not in after.get("hooks", {})
    assert "UserPromptSubmit" not in after.get("hooks", {})
    # statusLine removed
    assert "statusLine" not in after
    # Unrelated keys preserved
    assert after["permissions"] == {"allow": ["read"]}
    # Marker written
    assert (coach_dir / ".cli-uninstalled-by-plugin").exists()

    out = capsys.readouterr().out
    assert "Removed 2 CLI hook entries" in out
    assert "Removed CLI statusLine" in out


def test_preserves_plugin_hooks(tmp_path, env_under_plugin):
    """Plugin's own hook entries (which include plugin_root in command)
    must survive the strip."""
    plugin_root, coach_dir = env_under_plugin
    base = _settings_with_cli_hooks_and_statusline()
    plugin_cmd = f"{plugin_root}/bin/bootstrap.sh {plugin_root}/hooks/coach-session-start.py"
    base["hooks"]["SessionStart"][0]["hooks"].append({
        "type": "command",
        "command": plugin_cmd,
    })
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(base))

    sp.main(["--settings", str(settings)])
    after = json.loads(settings.read_text())
    sessions = after["hooks"]["SessionStart"][0]["hooks"]
    # Only plugin command remains
    assert len(sessions) == 1
    assert plugin_cmd in sessions[0]["command"]


def test_preserves_user_custom_statusline(tmp_path, env_under_plugin):
    """If the user has a non-Coach statusLine, leave it alone."""
    plugin_root, _ = env_under_plugin
    settings_data = _settings_with_cli_hooks_and_statusline()
    settings_data["statusLine"] = {
        "type": "command",
        "command": "bash /opt/my-custom-statusline.sh",
    }
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(settings_data))

    sp.main(["--settings", str(settings)])
    after = json.loads(settings.read_text())
    assert after["statusLine"]["command"] == "bash /opt/my-custom-statusline.sh"


def test_noop_when_nothing_to_remove(tmp_path, env_under_plugin, capsys):
    plugin_root, coach_dir = env_under_plugin
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_plugin_hooks(str(plugin_root))))

    rc = sp.main(["--settings", str(settings)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to do" in out.lower()
    # No marker written when nothing changed
    assert not (coach_dir / ".cli-uninstalled-by-plugin").exists()


def test_dry_run_does_not_write(tmp_path, env_under_plugin, capsys):
    plugin_root, coach_dir = env_under_plugin
    settings = tmp_path / "settings.json"
    raw = json.dumps(_settings_with_cli_hooks_and_statusline())
    settings.write_text(raw)
    mtime_before = settings.stat().st_mtime_ns

    rc = sp.main(["--settings", str(settings), "--dry-run"])
    assert rc == 0
    assert settings.stat().st_mtime_ns == mtime_before
    assert settings.read_text() == raw
    out = capsys.readouterr().out
    assert "Would remove" in out
    # Marker NOT written on dry-run
    assert not (coach_dir / ".cli-uninstalled-by-plugin").exists()


def test_clears_stale_defer_marker(tmp_path, env_under_plugin):
    """When the user explicitly switches, any prior .plugin-deferred
    marker should be cleared (CLI hooks are gone, plugin is now in
    charge — no reason for the deferred state to linger)."""
    plugin_root, coach_dir = env_under_plugin
    coach_dir.mkdir()
    (coach_dir / ".plugin-deferred").write_text(json.dumps({"reason": "stale"}))

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_cli_hooks_and_statusline()))

    sp.main(["--settings", str(settings)])
    assert not (coach_dir / ".plugin-deferred").exists()
    assert (coach_dir / ".cli-uninstalled-by-plugin").exists()


def test_settings_missing_returns_1(tmp_path, env_under_plugin, capsys):
    """No settings.json → exit 1 + clear error. Don't create one."""
    rc = sp.main(["--settings", str(tmp_path / "no-such.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_malformed_settings_returns_2(tmp_path, env_under_plugin, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text("{ not valid json")
    rc = sp.main(["--settings", str(settings)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not valid json" in err.lower() or "json" in err.lower()


def test_atomic_no_partial_write_on_failure(tmp_path, env_under_plugin, monkeypatch):
    """If os.replace raises mid-write, settings.json must not be
    truncated."""
    settings = tmp_path / "settings.json"
    raw = json.dumps(_settings_with_cli_hooks_and_statusline())
    settings.write_text(raw)
    original = json.loads(raw)

    def boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(sp.os, "replace", boom)

    with pytest.raises(OSError):
        sp.main(["--settings", str(settings)])

    # Original file content preserved (atomic semantics).
    after = json.loads(settings.read_text())
    assert after == original
