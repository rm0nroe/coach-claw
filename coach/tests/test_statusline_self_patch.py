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


@pytest.fixture(autouse=True)
def _isolate_coach_dir(tmp_path, monkeypatch):
    """v0.1.22+: ensure_statusline_installed now writes a trampoline
    + .plugin-root cache under coach_dir on every call. Without
    isolation, tests would scribble into the real ~/.claude/coach. Set
    COACH_CONFIG_DIR so resolve_coach_dir() returns the per-test
    tmpdir for any test that doesn't pass coach_dir explicitly."""
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "coach"))


@pytest.fixture
def settings(tmp_path):
    """Return a settings.json path inside tmp_path. Caller writes it."""
    return tmp_path / "settings.json"


@pytest.fixture
def plugin_root(tmp_path):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    return root


@pytest.fixture
def coach_dir(tmp_path):
    """Trampoline + marker dir under tmp_path (matches the path the
    autouse fixture sets via COACH_CONFIG_DIR)."""
    return tmp_path / "coach"


def test_inserts_statusline_when_absent(settings, plugin_root, coach_dir):
    settings.write_text(json.dumps({}))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "installed"

    written = json.loads(settings.read_text())
    assert "statusLine" in written
    cmd = written["statusLine"]["command"]
    # v0.1.22+: command routes through the stable trampoline under
    # coach_dir, NOT the versioned plugin_root path. The trampoline
    # script + .plugin-root cache are written under coach_dir.
    assert sp.TRAMPOLINE_NAME in cmd
    assert "default_statusline.py" in cmd
    # plugin_root must NOT appear directly in the settings.json command —
    # that's the whole point of the trampoline indirection.
    assert str(plugin_root) not in cmd
    # Absolute path (no ${CLAUDE_PLUGIN_ROOT} placeholder; that wouldn't
    # expand inside settings.json).
    assert "${CLAUDE_PLUGIN_ROOT}" not in cmd
    # Trampoline + cache file written under coach_dir.
    assert (coach_dir / sp.TRAMPOLINE_NAME).exists()
    cache = (coach_dir / sp.PLUGIN_ROOT_CACHE_NAME).read_text().strip()
    assert cache == str(plugin_root.resolve())


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


def test_recognizes_wrapped_statusline_as_ours(settings, plugin_root, coach_dir, tmp_path):
    """ours-wrapped pointing at the CURRENT trampoline shape → matched
    no-op (no rewrite). Legacy versioned-plugin wrap commands are now
    migrated on encounter; only trampoline-shape commands are stable."""
    # Pre-write a wrap marker so the action treats it as ours-wrapped.
    coach_dir.mkdir(parents=True, exist_ok=True)
    (coach_dir / ".statusline-wrap.json").write_text(json.dumps({
        "original_command": "bash /opt/x.sh",
    }))
    trampoline_path = coach_dir / sp.TRAMPOLINE_NAME
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"bash {trampoline_path} statusline_wrap.py",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "matched"


def test_refreshes_stale_plugin_path_on_wrapped(
    settings, plugin_root, coach_dir
):
    """ours-wrapped pointing at a stale legacy versioned plugin path
    → patcher rewrites to the stable trampoline shape under coach_dir
    and refreshes .plugin-root to the current plugin_root."""
    # Pretend an older plugin dir wrote a legacy versioned entry.
    old_root = plugin_root.parent / "plugin-old"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"{old_root}/bin/run.sh {old_root}/bin/statusline_wrap.py",
        },
    }))
    # Wrap marker so the action recognizes ours-wrapped.
    coach_dir.mkdir(parents=True, exist_ok=True)
    (coach_dir / ".statusline-wrap.json").write_text(json.dumps({
        "original_command": "bash /opt/x.sh",
    }))

    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "wrap-refreshed"
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert sp.TRAMPOLINE_NAME in new_cmd
    assert str(old_root) not in new_cmd
    # plugin_root is no longer embedded in settings.json (it lives in
    # the trampoline's .plugin-root cache instead).
    assert str(plugin_root) not in new_cmd
    cache = (coach_dir / sp.PLUGIN_ROOT_CACHE_NAME).read_text().strip()
    assert cache == str(plugin_root.resolve())


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


def test_recognizes_trampoline_statusline_as_ours(settings, plugin_root, coach_dir):
    """v0.1.22+: trampoline-shape default command is the stable shape;
    a second SessionStart with the SAME trampoline path is a matched
    no-op (cache file gets refreshed in place but settings.json is
    untouched)."""
    trampoline_path = coach_dir / sp.TRAMPOLINE_NAME
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"bash {trampoline_path} default_statusline.py",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "matched"


def test_migrates_legacy_versioned_plugin_default(settings, plugin_root, coach_dir):
    """v0.1.22+: any legacy plugin-default shape (versioned run.sh path
    or pre-v0.1.18 bootstrap.sh path) gets migrated to the stable
    trampoline shape under coach_dir. This is the load-bearing fix for
    the recurring 'Plugin directory does not exist: .../<old-version>'
    error that fires when a long-running CC session holds a stale
    CLAUDE_PLUGIN_ROOT after /plugin update."""
    # Legacy versioned shape — what every coach-claw <=0.1.21 wrote.
    old_root = plugin_root.parent / "plugin-OLD-version"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"{old_root}/bin/run.sh {old_root}/bin/default_statusline.py",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "refreshed-path"
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert sp.TRAMPOLINE_NAME in new_cmd
    assert "default_statusline.py" in new_cmd
    # Old versioned path scrubbed.
    assert str(old_root) not in new_cmd


def test_migrates_legacy_bootstrap_to_trampoline(settings, plugin_root, coach_dir):
    """Pre-v0.1.18 plugin entries pointed at bootstrap.sh; under v0.1.22+
    these are migrated to the stable trampoline shape, same path as
    fresh installs."""
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": f"{plugin_root}/bin/bootstrap.sh {plugin_root}/bin/default_statusline.py",
        },
    }))
    result = sp.ensure_statusline_installed(str(plugin_root), settings_path=settings)
    assert result == "refreshed-path"
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert sp.TRAMPOLINE_NAME in new_cmd
    assert "bootstrap.sh" not in new_cmd
    assert "default_statusline.py" in new_cmd


def test_desired_entry_quotes_paths_with_spaces(tmp_path):
    """Legacy fallback (coach_dir=None): a plugin_root with spaces must
    produce a command that bash parses as exactly two tokens (run.sh +
    default_statusline.py). Kept as a regression guard for callers that
    haven't been migrated to the trampoline shape yet."""
    plugin_root = tmp_path / "Plugin Dir With Spaces"
    (plugin_root / "bin").mkdir(parents=True)
    entry = sp._desired_entry(plugin_root)

    tokens = shlex.split(entry["command"])
    assert len(tokens) == 2, (
        f"plugin_root paths split by bash; tokens={tokens!r} "
        f"command={entry['command']!r}"
    )
    assert tokens[0] == str(plugin_root / "bin" / "run.sh")
    assert tokens[1] == str(plugin_root / "bin" / "default_statusline.py")


def test_desired_entry_trampoline_quotes_paths_with_spaces(tmp_path):
    """Trampoline shape (coach_dir set): a coach_dir with spaces must
    produce a command that bash parses as `bash <quoted-trampoline>
    default_statusline.py` — three tokens."""
    coach_dir = tmp_path / "Coach Dir With Spaces"
    plugin_root = tmp_path / "plugin"
    (plugin_root / "bin").mkdir(parents=True)
    entry = sp._desired_entry(plugin_root, coach_dir=coach_dir)

    tokens = shlex.split(entry["command"])
    assert tokens == [
        "bash",
        str(coach_dir / sp.TRAMPOLINE_NAME),
        "default_statusline.py",
    ]


def test_ensure_trampoline_installed_writes_files(tmp_path):
    """ensure_trampoline_installed writes the shell script + .plugin-root
    cache and the contents are exactly what the trampoline expects."""
    coach_dir = tmp_path / "coach"
    plugin_root = tmp_path / "plugin" / "0.1.22"
    plugin_root.mkdir(parents=True)

    result = sp.ensure_trampoline_installed(coach_dir, plugin_root)
    assert result == coach_dir / sp.TRAMPOLINE_NAME

    assert (coach_dir / sp.TRAMPOLINE_NAME).exists()
    body = (coach_dir / sp.TRAMPOLINE_NAME).read_text()
    # Must read the .plugin-root cache file the same module wrote.
    assert sp.PLUGIN_ROOT_CACHE_NAME in body
    # Must honor COACH_CONFIG_DIR so tests + npm CLI wrapper work.
    assert "COACH_CONFIG_DIR" in body
    # Must exec into the plugin's bin/run.sh + $TARGET, preserving stdin.
    assert "exec" in body
    assert "bin/run.sh" in body

    cache = (coach_dir / sp.PLUGIN_ROOT_CACHE_NAME).read_text().strip()
    assert cache == str(plugin_root.resolve())


def test_ensure_trampoline_installed_refreshes_stale_cache(tmp_path):
    """ensure_trampoline_installed rewrites .plugin-root when the
    active plugin path moves (/plugin update bumps the version dir)."""
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    # Pre-populate with a stale path.
    (coach_dir / sp.PLUGIN_ROOT_CACHE_NAME).write_text(
        "/old/stale/plugin/0.1.19\n"
    )
    plugin_root = tmp_path / "plugin" / "0.1.22"
    plugin_root.mkdir(parents=True)

    sp.ensure_trampoline_installed(coach_dir, plugin_root)
    cache = (coach_dir / sp.PLUGIN_ROOT_CACHE_NAME).read_text().strip()
    assert cache == str(plugin_root.resolve())


def test_ensure_trampoline_installed_idempotent(tmp_path):
    """Calling twice with the same args is a no-op on the second call
    (content already matches)."""
    coach_dir = tmp_path / "coach"
    plugin_root = tmp_path / "plugin" / "0.1.22"
    plugin_root.mkdir(parents=True)

    sp.ensure_trampoline_installed(coach_dir, plugin_root)
    mtime1 = (coach_dir / sp.TRAMPOLINE_NAME).stat().st_mtime_ns

    # Second call — content equal → no rewrite.
    sp.ensure_trampoline_installed(coach_dir, plugin_root)
    mtime2 = (coach_dir / sp.TRAMPOLINE_NAME).stat().st_mtime_ns
    assert mtime1 == mtime2
