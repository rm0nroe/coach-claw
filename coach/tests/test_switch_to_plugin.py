"""switch_to_plugin.py — flip Coach control from npm CLI to plugin.

Pairs with /coach-claw:switch skill. Removes CLI-installed hook entries
from settings.json, optionally clears the CLI's statusLine, writes a
marker so /coach-claw:doctor knows the user explicitly switched.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
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


# ---------------------------------------------------------------------------
# Wrap-shape policy (v0.1.4)
# ---------------------------------------------------------------------------


def test_rewrites_cli_wrap_to_plugin_shape(tmp_path, env_under_plugin):
    """ours-wrapped pointing at the CLI trampoline → switch rewrites it
    to point at the plugin's bootstrap.sh + statusline_wrap.py. Saved
    original (`.statusline-wrap.json`) is untouched — coach state is
    shared between distributions."""
    plugin_root, _ = env_under_plugin
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash /Users/foo/.claude/coach/default-statusline-wrap-command.sh",
        },
    }))
    rc = sp.main(["--settings", str(settings)])
    assert rc == 0

    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert str(plugin_root) in new_cmd
    assert "bootstrap.sh" in new_cmd
    assert "statusline_wrap.py" in new_cmd
    assert "default-statusline-wrap-command.sh" not in new_cmd


def test_noop_when_already_plugin_wrap_shape(tmp_path, env_under_plugin):
    """If the wrap shape already points at the plugin, switch is a no-op
    (no double-rewrite, no churn)."""
    plugin_root, _ = env_under_plugin
    plugin_cmd = (
        f"{plugin_root}/bin/bootstrap.sh {plugin_root}/bin/statusline_wrap.py"
    )
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": plugin_cmd},
    }))
    rc = sp.main(["--settings", str(settings)])
    assert rc == 0

    after = json.loads(settings.read_text())["statusLine"]["command"]
    assert after == plugin_cmd  # exact byte match — no rewrite


def test_leaves_integrated_externally_alone(tmp_path, env_under_plugin):
    """When statusLine is a custom user command and the opt-out marker
    says `already-integrated`, switch must NOT install a default plugin
    statusline — the user already integrates Coach themselves."""
    _, coach_dir = env_under_plugin
    coach_dir.mkdir(parents=True, exist_ok=True)
    (coach_dir / ".statusline-wrap-disabled").write_text(json.dumps({
        "reason": "already-integrated",
        "detected_in": "/Users/foo/.claude/statusline-command.sh",
    }))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash /Users/foo/.claude/statusline-command.sh",
        },
    }))
    rc = sp.main(["--settings", str(settings)])
    assert rc == 0

    after = json.loads(settings.read_text())
    # statusLine preserved exactly
    assert after["statusLine"]["command"] == "bash /Users/foo/.claude/statusline-command.sh"


# ---------------------------------------------------------------------------
# Shell-safety of the plugin-shape rewrite (v0.1.6 fix)
# ---------------------------------------------------------------------------


def test_rewrite_quotes_plugin_paths_with_spaces(tmp_path, monkeypatch):
    """Same defect class as v0.1.5's _build_wrapper_command +
    _desired_entry: a CLAUDE_PLUGIN_ROOT containing spaces must produce
    a settings.json command string that bash parses as exactly two
    tokens. Pre-v0.1.6 the unquoted f-string interpolation generated
    `/tmp/.../Plugin Dir/bin/bootstrap.sh /tmp/.../Plugin Dir/bin/...`
    which shlex.split breaks into 4+ tokens, ENOENT'ing under bash."""
    plugin_root = tmp_path / "Plugin Dir With Spaces"
    (plugin_root / "bin").mkdir(parents=True)
    coach_dir = tmp_path / "coach"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash /Users/foo/.claude/coach/default-statusline-wrap-command.sh",
        },
    }))
    rc = sp.main(["--settings", str(settings)])
    assert rc == 0

    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    tokens = shlex.split(new_cmd)
    assert len(tokens) == 2, (
        f"plugin_root paths split by bash; tokens={tokens!r} cmd={new_cmd!r}"
    )
    assert tokens[0] == str(plugin_root / "bin" / "bootstrap.sh")
    assert tokens[1] == str(plugin_root / "bin" / "statusline_wrap.py")


def test_rewrite_command_executes_under_bash_with_spaces(tmp_path, monkeypatch):
    """End-to-end: the rewritten command must actually run under
    `bash -c` without ENOENT when CLAUDE_PLUGIN_ROOT has a space.
    Mirrors test_install_auto_wraps_claimed_statusline's exec guard
    from v0.1.5."""
    plugin_root = tmp_path / "Plugin Dir With Spaces"
    plugin_bin = plugin_root / "bin"
    plugin_bin.mkdir(parents=True)
    # Stand-in scripts so bash actually has something to exec on the
    # generated command path. Doesn't matter what they print — just
    # has to be findable + executable.
    (plugin_bin / "bootstrap.sh").write_text("#!/bin/bash\nexec \"$@\"\n")
    (plugin_bin / "statusline_wrap.py").write_text(
        "#!/usr/bin/env python3\nprint('ok')\n"
    )
    (plugin_bin / "bootstrap.sh").chmod(0o755)
    (plugin_bin / "statusline_wrap.py").chmod(0o755)

    coach_dir = tmp_path / "coach"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash /tmp/cli/coach/default-statusline-wrap-command.sh",
        },
    }))
    sp.main(["--settings", str(settings)])

    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    bash_path = shutil.which("bash")
    assert bash_path
    proc = subprocess.run(
        [bash_path, "-c", new_cmd],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"rewritten command failed under bash -c — likely unquoted "
        f"path with spaces.\ncommand={new_cmd!r}\nstderr={proc.stderr!r}"
    )
    assert "No such file or directory" not in proc.stderr, proc.stderr
