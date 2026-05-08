"""coexistence_check.py — detect when CLI hooks are registered so the
plugin can self-defer.

CLI distribution has Coach hooks at ~/.claude/hooks/coach-*.py registered
in settings.json. Plugin distribution has them at
${CLAUDE_PLUGIN_ROOT}/hooks/coach-*.py registered via hooks.json. A
user with both installed must NOT get double-fires. The check returns
exit code 10 when CLI hooks are present so bootstrap.sh can defer.

Unit tests only — bootstrap.sh integration is in
tests/plugin/test_coexistence_integration.py.
"""
from __future__ import annotations

import json

import coexistence_check as cc


def _settings_with_cli_hooks() -> dict:
    """Synthesize an install.sh-style settings.json — hooks point at
    absolute paths under ~/.claude/hooks/, NOT under any plugin root."""
    return {
        "hooks": {
            "SessionStart": [{"hooks": [{
                "type": "command",
                "command": "/usr/bin/python3 /Users/foo/.claude/hooks/coach-session-start.py",
            }]}],
            "UserPromptSubmit": [{"hooks": [{
                "type": "command",
                "command": "/usr/bin/python3 /Users/foo/.claude/hooks/coach-user-prompt.py",
            }]}],
        }
    }


def _settings_with_plugin_hooks(plugin_root: str) -> dict:
    """Plugin-style: hook commands include the plugin root path."""
    cmd = f"{plugin_root}/bin/bootstrap.sh {plugin_root}/hooks/coach-session-start.py"
    return {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}],
        }
    }


def test_returns_0_when_settings_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(tmp_path / "no-such.json"))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
    assert cc.main() == 0


def test_returns_0_when_no_hooks_block(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"permissions": {}}))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
    assert cc.main() == 0


def test_returns_0_when_only_plugin_hooks(tmp_path, monkeypatch):
    """Plugin's own hooks present but no CLI hooks → no defer."""
    plugin_root = str(tmp_path / "plugin")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_plugin_hooks(plugin_root)))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root)
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "coach"))
    assert cc.main() == 0


def test_returns_10_when_cli_hooks_present(tmp_path, monkeypatch):
    """CLI-style hook entries (no plugin root in command) → defer."""
    plugin_root = str(tmp_path / "plugin")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_cli_hooks()))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root)
    coach_dir = tmp_path / "coach"
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    assert cc.main() == 10
    # Defer marker written
    marker = coach_dir / ".plugin-deferred"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert "deferred_at" in payload
    assert payload["reason"] == "cli-hooks-detected"


def test_returns_0_for_unrelated_hooks(tmp_path, monkeypatch):
    """A user has some OTHER tool's hooks registered. No coach pattern
    in the commands → no defer."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "SessionStart": [{"hooks": [{
                "type": "command",
                "command": "echo 'some other tool fired'",
            }]}],
        }
    }))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "coach"))
    assert cc.main() == 0


def test_returns_0_on_malformed_settings(tmp_path, monkeypatch):
    """Malformed JSON → fail-safe to 0 (no defer). Better to risk
    double-fire than to silently disable the plugin on a parse error
    that has nothing to do with us."""
    settings = tmp_path / "settings.json"
    settings.write_text("{ not valid json")
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
    assert cc.main() == 0


def test_returns_10_when_plugin_root_unset_but_cli_hooks_present(tmp_path, monkeypatch):
    """If CLAUDE_PLUGIN_ROOT is somehow unset (shouldn't happen at
    runtime but be defensive), any coach hook command in settings.json
    is treated as CLI."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_cli_hooks()))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "coach"))
    assert cc.main() == 10
