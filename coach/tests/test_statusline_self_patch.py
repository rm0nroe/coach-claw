"""statusline_self_patch.ensure_statusline_installed — plugin self-patch
of ~/.claude/settings.json:statusLine.

Plugin distribution only. Gating happens at the call site
(coach-session-start.py:_maybe_install_plugin_statusline checks
CLAUDE_PLUGIN_ROOT). These tests exercise the patcher directly with a
tmpdir settings.json.
"""
from __future__ import annotations

import json
import shlex
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


def test_auto_wraps_when_other_statusline_present(
    settings, plugin_root, tmp_path, monkeypatch
):
    """v0.1.4: claimed statusLine on first encounter → auto-wrap (instead
    of leaving alone). Original is saved to .statusline-wrap.json and the
    wrapper command replaces it in settings.json."""
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))

    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash /custom/user-thing.sh"},
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "wrapped"
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert "statusline_wrap.py" in new_cmd
    saved = json.loads((coach_dir / ".statusline-wrap.json").read_text())
    assert saved["original_command"] == "bash /custom/user-thing.sh"


def test_claimed_when_optout_marker_present(
    settings, plugin_root, tmp_path, monkeypatch, capfd
):
    """User explicitly unwrapped earlier → opt-out marker exists → patcher
    leaves the user's claimed statusLine alone (no auto-wrap, no rewrite)."""
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    (coach_dir / ".statusline-wrap-disabled").write_text(json.dumps({
        "reason": "user-unwrapped",
    }))

    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash /custom/user-thing.sh"},
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "claimed"
    # User's command preserved
    assert json.loads(settings.read_text())["statusLine"]["command"] == "bash /custom/user-thing.sh"


def test_claimed_when_user_script_integrates_coach(
    settings, plugin_root, tmp_path, monkeypatch
):
    """Manual-Coach pre-flight: user's script already calls coach/bin/stats.py
    → patcher detects, writes opt-out marker, leaves statusLine alone."""
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))

    user_script = tmp_path / "statusline-command.sh"
    user_script.write_text(
        "#!/bin/bash\nexec coach/bin/stats.py\n"
    )
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": f"bash {user_script}"},
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "claimed"
    # Opt-out marker auto-written by manual-Coach pre-flight
    disabled = json.loads((coach_dir / ".statusline-wrap-disabled").read_text())
    assert disabled["reason"] == "already-integrated"


def test_recognizes_wrapped_statusline_as_ours(settings, plugin_root):
    """ours-wrapped pointing at the CURRENT plugin_root → matched no-op
    (the wrap shape is recognized as ours, no rewrite)."""
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"{plugin_root}/bin/bootstrap.sh {plugin_root}/bin/statusline_wrap.py",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "matched"


def test_refreshes_stale_plugin_path_on_wrapped(
    settings, plugin_root, tmp_path, monkeypatch
):
    """ours-wrapped pointing at a stale plugin version dir → patcher
    rewrites with the current plugin_root."""
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    # Pretend an older plugin dir wrote the entry
    old_root = tmp_path / "plugin-old"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"{old_root}/bin/bootstrap.sh {old_root}/bin/statusline_wrap.py",
        },
    }))
    # Also need a wrap marker so the action recognizes ours-wrapped
    (coach_dir / ".statusline-wrap.json").write_text(json.dumps({
        "original_command": "bash /opt/x.sh",
    }))

    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "wrap-refreshed"
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert str(plugin_root) in new_cmd
    assert str(old_root) not in new_cmd


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


def test_desired_entry_quotes_paths_with_spaces(tmp_path):
    """Symmetric with `_build_wrapper_command` in statusline_wrap_action:
    a plugin_root with spaces must produce a command that bash parses
    as exactly two tokens (bootstrap.sh + default_statusline.py)."""
    plugin_root = tmp_path / "Plugin Dir With Spaces"
    (plugin_root / "bin").mkdir(parents=True)
    entry = sp._desired_entry(plugin_root)

    tokens = shlex.split(entry["command"])
    assert len(tokens) == 2, (
        f"plugin_root paths split by bash; tokens={tokens!r} "
        f"command={entry['command']!r}"
    )
    assert tokens[0] == str(plugin_root / "bin" / "bootstrap.sh")
    assert tokens[1] == str(plugin_root / "bin" / "default_statusline.py")
