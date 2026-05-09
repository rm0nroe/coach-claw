"""Tests for coach/bin/doctor.py — the plugin diagnostic surface.

Each probe is exercised via monkeypatching env / files / subprocess, so
the test suite never depends on the running box's actual plugin install,
settings.json, launchd state, or venv.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import doctor


# ---------------------------------------------------------------------------
# probe_plugin_install
# ---------------------------------------------------------------------------

def test_plugin_install_absent_when_no_installed_plugins_json(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "INSTALLED_PLUGINS_PATH", tmp_path / "missing.json")
    r = doctor.probe_plugin_install()
    assert r["status"] == "absent"
    assert r["entries"] == []


def test_plugin_install_absent_when_no_coach_claw_entries(tmp_path, monkeypatch):
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({
        "version": 2,
        "plugins": {"some-other@market": [{"version": "1.0", "scope": "user"}]},
    }))
    monkeypatch.setattr(doctor, "INSTALLED_PLUGINS_PATH", p)
    r = doctor.probe_plugin_install()
    assert r["status"] == "absent"
    assert "no coach-claw entries" in r["detail"]


def test_plugin_install_ok_with_single_entry(tmp_path, monkeypatch):
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({
        "version": 2,
        "plugins": {
            "coach-claw@coach-claw-plugins": [
                {
                    "scope": "user",
                    "installPath": "/path/to/plugin/0.1.2",
                    "version": "0.1.2",
                    "lastUpdated": "2026-05-09T00:00:00Z",
                },
            ],
        },
    }))
    monkeypatch.setattr(doctor, "INSTALLED_PLUGINS_PATH", p)
    r = doctor.probe_plugin_install()
    assert r["status"] == "ok"
    assert len(r["entries"]) == 1
    e = r["entries"][0]
    assert e["plugin_id"] == "coach-claw@coach-claw-plugins"
    assert e["marketplace"] == "coach-claw-plugins"
    assert e["version"] == "0.1.2"


def test_plugin_install_warn_when_multiple_entries(tmp_path, monkeypatch):
    """Two marketplaces serving coach-claw is suspicious; report warn."""
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({
        "version": 2,
        "plugins": {
            "coach-claw@coach-claw-plugins": [{"version": "0.1.2", "scope": "user"}],
            "coach-claw@my-fork": [{"version": "0.1.0", "scope": "user"}],
        },
    }))
    monkeypatch.setattr(doctor, "INSTALLED_PLUGINS_PATH", p)
    r = doctor.probe_plugin_install()
    assert r["status"] == "warn"
    assert len(r["entries"]) == 2


def test_plugin_install_error_on_malformed_json(tmp_path, monkeypatch):
    p = tmp_path / "installed_plugins.json"
    p.write_text("{not json")
    monkeypatch.setattr(doctor, "INSTALLED_PLUGINS_PATH", p)
    r = doctor.probe_plugin_install()
    assert r["status"] == "error"
    assert r["entries"] == []


# ---------------------------------------------------------------------------
# probe_coexistence
# ---------------------------------------------------------------------------

def test_coexistence_active_when_no_marker(tmp_path):
    r = doctor.probe_coexistence(tmp_path)
    assert r["status"] == "active"
    assert r["deferred_at"] == ""


def test_coexistence_deferred_when_marker_present(tmp_path):
    marker = tmp_path / ".plugin-deferred"
    marker.write_text(json.dumps({
        "deferred_at": "2026-05-09T00:00:00Z",
        "reason": "cli-hooks-detected",
    }))
    r = doctor.probe_coexistence(tmp_path)
    assert r["status"] == "deferred"
    assert r["deferred_at"] == "2026-05-09T00:00:00Z"
    assert r["reason"] == "cli-hooks-detected"


def test_coexistence_surfaces_cli_removed_marker(tmp_path):
    (tmp_path / ".plugin-deferred").write_text(json.dumps({"deferred_at": "x", "reason": "y"}))
    (tmp_path / ".cli-uninstalled-by-plugin").write_text(json.dumps({"uninstalled_at": "z"}))
    r = doctor.probe_coexistence(tmp_path)
    assert r["status"] == "deferred"
    assert r["cli_removed_marker"] is not None


def test_coexistence_handles_unparseable_marker(tmp_path):
    """Malformed marker JSON shouldn't blow up the probe."""
    (tmp_path / ".plugin-deferred").write_text("{not json")
    r = doctor.probe_coexistence(tmp_path)
    assert r["status"] == "deferred"


# ---------------------------------------------------------------------------
# _classify_statusline + probe_statusline
# ---------------------------------------------------------------------------

def test_classify_plugin_statusline():
    r = doctor._classify_statusline({
        "type": "command",
        "command": "/path/to/plugin/bin/bootstrap.sh /path/to/plugin/bin/default_statusline.py",
    })
    assert r["ownership"] == "ours-plugin"


def test_classify_cli_statusline():
    r = doctor._classify_statusline({
        "type": "command",
        "command": "bash ~/.claude/coach/default-statusline-command.sh",
    })
    assert r["ownership"] == "ours-cli"


def test_classify_user_custom_statusline():
    r = doctor._classify_statusline({
        "type": "command",
        "command": "bash ~/.claude/statusline-command.sh",
    })
    assert r["ownership"] == "claimed"


def test_classify_absent_statusline():
    assert doctor._classify_statusline(None)["ownership"] == "absent"
    assert doctor._classify_statusline("not-a-dict")["ownership"] == "absent"


def test_probe_statusline_absent_when_no_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "SETTINGS_PATH", tmp_path / "missing.json")
    r = doctor.probe_statusline()
    assert r["status"] == "absent"
    assert r["ownership"] == "absent"


def test_probe_statusline_ok_when_plugin_owned(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "/p/bin/bootstrap.sh /p/bin/default_statusline.py",
        },
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.probe_statusline()
    assert r["status"] == "ok"
    assert r["ownership"] == "ours-plugin"


def test_probe_statusline_claimed_when_user_custom(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash ~/.claude/my-line.sh"},
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.probe_statusline()
    assert r["status"] == "claimed"
    assert r["ownership"] == "claimed"


def test_probe_statusline_error_on_malformed_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.probe_statusline()
    assert r["status"] == "error"


# ---------------------------------------------------------------------------
# probe_cron — light wrapper test (the underlying cron_check has its own suite)
# ---------------------------------------------------------------------------

def test_probe_cron_ok_when_registered(monkeypatch):
    monkeypatch.setattr(doctor, "is_cron_registered", lambda: True)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    r = doctor.probe_cron()
    assert r["status"] == "ok"
    assert r["registered"] is True


def test_probe_cron_missing_when_not_registered(monkeypatch):
    monkeypatch.setattr(doctor, "is_cron_registered", lambda: False)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    r = doctor.probe_cron()
    assert r["status"] == "missing"
    assert r["registered"] is False
    assert "launchd" in r["detail"]


def test_probe_cron_skipped_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    r = doctor.probe_cron()
    assert r["status"] == "skipped"


# ---------------------------------------------------------------------------
# probe_venv
# ---------------------------------------------------------------------------

def test_probe_venv_missing_when_no_python(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    r = doctor.probe_venv()
    assert r["status"] == "missing"
    assert "no python3" in r["detail"]


def test_probe_venv_ok_when_yaml_imports(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python3"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))

    def fake_run(*args, **kwargs):
        return MagicMock(returncode=0, stdout="6.0.3\n", stderr="")
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    r = doctor.probe_venv()
    assert r["status"] == "ok"
    assert r["yaml_version"] == "6.0.3"


def test_probe_venv_broken_when_yaml_import_fails(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python3"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))

    def fake_run(*args, **kwargs):
        return MagicMock(returncode=1, stdout="", stderr="ModuleNotFoundError: yaml\n")
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    r = doctor.probe_venv()
    assert r["status"] == "broken"


# ---------------------------------------------------------------------------
# remove_statusline
# ---------------------------------------------------------------------------

def test_remove_statusline_no_op_when_no_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "SETTINGS_PATH", tmp_path / "missing.json")
    r = doctor.remove_statusline()
    assert r["result"] == "no-op"


def test_remove_statusline_no_op_when_no_key(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.remove_statusline()
    assert r["result"] == "no-op"
    # Ensure nothing was written.
    assert json.loads(settings.read_text()) == {"theme": "dark"}


def test_remove_statusline_skipped_when_user_custom(tmp_path, monkeypatch):
    """Critical safety: must not clear a non-Coach statusLine."""
    settings = tmp_path / "settings.json"
    original = {
        "statusLine": {"type": "command", "command": "bash ~/.claude/my-line.sh"},
    }
    settings.write_text(json.dumps(original))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.remove_statusline()
    assert r["result"] == "skipped"
    # Settings unchanged.
    assert json.loads(settings.read_text()) == original


def test_remove_statusline_skipped_when_integrated_externally(tmp_path, monkeypatch):
    """v0.1.5 regression guard: integrated-externally is a non-Coach
    command (user's own script, just one that calls Coach internally).
    --remove-statusline must NOT delete it.

    Pre-v0.1.5 this fell through the `claimed`-only guard at
    doctor.py:413 and got wiped by the `# ours-* — safe to clear`
    branch — Ryan-style custom statuslines disappeared.
    """
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))

    # Opt-out marker tagged `already-integrated` is what flips the
    # classifier from `claimed` to `integrated-externally`.
    (coach_dir / ".statusline-wrap-disabled").write_text(json.dumps({
        "reason": "already-integrated",
        "detected_in": "/Users/foo/.claude/statusline-command.sh",
    }))

    settings = tmp_path / "settings.json"
    original = {
        "statusLine": {
            "type": "command",
            "command": "bash /Users/foo/.claude/statusline-command.sh",
        },
    }
    settings.write_text(json.dumps(original))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)

    r = doctor.remove_statusline()
    assert r["result"] == "skipped", (
        f"integrated-externally must be protected; got {r!r}"
    )
    assert json.loads(settings.read_text()) == original, (
        "settings.json:statusLine was mutated — Ryan-style custom "
        "statusline got wiped"
    )


def test_remove_statusline_clears_plugin_owned(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "/p/bin/bootstrap.sh /p/bin/default_statusline.py",
        },
        "theme": "dark",
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.remove_statusline()
    assert r["result"] == "removed"
    written = json.loads(settings.read_text())
    assert "statusLine" not in written
    assert written["theme"] == "dark"  # other keys preserved


def test_remove_statusline_clears_cli_owned(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "bash ~/.claude/coach/default-statusline-command.sh",
        },
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.remove_statusline()
    assert r["result"] == "removed"
    assert "statusLine" not in json.loads(settings.read_text())


def test_remove_statusline_error_on_malformed(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.remove_statusline()
    assert r["result"] == "error"


# ---------------------------------------------------------------------------
# CLI entry — exit codes + arg validation
# ---------------------------------------------------------------------------

def test_cli_default_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "collect_probes", lambda: {
        "plugin_install": {"status": "absent", "detail": "x", "entries": []},
        "coexistence": {"status": "active", "detail": "x", "deferred_at": "", "reason": "", "cli_removed_marker": None},
        "statusline": {"status": "absent", "detail": "x", "ownership": "absent", "command": ""},
        "cron": {"status": "ok", "detail": "x", "registered": True, "platform": "Darwin"},
        "venv": {"status": "ok", "detail": "x", "venv_path": "/v", "yaml_version": "6.0"},
    })
    rc = doctor.main([])
    assert rc == 0


def test_cli_json_output_is_valid_json(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "collect_probes", lambda: {
        "plugin_install": {"status": "ok", "detail": "x", "entries": []},
        "coexistence": {"status": "active", "detail": "x", "deferred_at": "", "reason": "", "cli_removed_marker": None},
        "statusline": {"status": "ok", "detail": "x", "ownership": "ours-plugin", "command": "x"},
        "cron": {"status": "ok", "detail": "x", "registered": True, "platform": "Darwin"},
        "venv": {"status": "ok", "detail": "x", "venv_path": "/v", "yaml_version": "6.0"},
    })
    rc = doctor.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"plugin_install", "coexistence", "statusline", "cron", "venv"}


def test_cli_rejects_combined_flags(capsys):
    rc = doctor.main(["--json", "--remove-statusline"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot combine" in err


# ---------------------------------------------------------------------------
# Wrap-mode classification + actions (v0.1.4)
# ---------------------------------------------------------------------------


def test_classify_recognizes_wrapped_plugin_shape():
    """`bootstrap.sh statusline_wrap.py` (plugin) → ours-wrapped."""
    r = doctor._classify_statusline({
        "type": "command",
        "command": "/p/bin/bootstrap.sh /p/bin/statusline_wrap.py",
    })
    assert r["ownership"] == "ours-wrapped"


def test_classify_recognizes_wrapped_cli_shape():
    """`bash …/default-statusline-wrap-command.sh` (CLI) → ours-wrapped."""
    r = doctor._classify_statusline({
        "type": "command",
        "command": "bash /home/u/.claude/coach/default-statusline-wrap-command.sh",
    })
    assert r["ownership"] == "ours-wrapped"


def test_classify_integrated_externally_when_optout_marker_says_so(tmp_path):
    """User has a custom statusLine AND a `.statusline-wrap-disabled`
    marker with `reason: already-integrated` → integrated-externally."""
    (tmp_path / ".statusline-wrap-disabled").write_text(json.dumps({
        "reason": "already-integrated",
        "detected_in": "/home/u/.claude/statusline-command.sh",
    }))
    r = doctor._classify_statusline(
        {"type": "command", "command": "bash ~/.claude/statusline-command.sh"},
        coach_dir=tmp_path,
    )
    assert r["ownership"] == "integrated-externally"
    assert "/statusline-command.sh" in r["detected_in"]


def test_probe_statusline_wrapped_surfaces_saved_original(tmp_path, monkeypatch):
    """ours-wrapped → probe reads .statusline-wrap.json and exposes the
    original command alongside the wrapper."""
    settings = tmp_path / "settings.json"
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "/p/bin/bootstrap.sh /p/bin/statusline_wrap.py"},
    }))
    (coach_dir / ".statusline-wrap.json").write_text(json.dumps({
        "original_command": "bash /opt/saved.sh",
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.probe_statusline(coach_dir=coach_dir)
    assert r["ownership"] == "ours-wrapped"
    assert r["status"] == "ok"
    assert r["wrapped_original"] == "bash /opt/saved.sh"


def test_probe_statusline_integrated_externally_status_is_ok(tmp_path, monkeypatch):
    """Integrated-externally is a green-light state, not a warning."""
    settings = tmp_path / "settings.json"
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash /opt/x.sh"},
    }))
    (coach_dir / ".statusline-wrap-disabled").write_text(json.dumps({
        "reason": "already-integrated",
        "detected_in": "/opt/x.sh",
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    r = doctor.probe_statusline(coach_dir=coach_dir)
    assert r["ownership"] == "integrated-externally"
    assert r["status"] == "ok"
    assert "no wrap needed" in r["detail"].lower()


def test_render_report_includes_wrap_suggestion_on_claimed_row():
    """The claimed-row suggested-actions hint is the entire reason a user
    knows wrap mode exists."""
    probes = {
        "plugin_install": {"status": "ok", "detail": "x", "entries": []},
        "coexistence": {"status": "active", "detail": "x", "deferred_at": "", "reason": "", "cli_removed_marker": None},
        "statusline": {
            "status": "claimed",
            "detail": "statusLine points elsewhere; can be wrapped",
            "ownership": "claimed",
            "command": "bash ~/.claude/my-line.sh",
        },
        "cron": {"status": "ok", "detail": "x", "registered": True, "platform": "Darwin"},
        "venv": {"status": "ok", "detail": "x", "venv_path": "/v", "yaml_version": "6.0"},
    }
    out = doctor.render_report(probes)
    assert "--wrap-statusline" in out


def test_render_report_includes_unwrap_suggestion_on_wrapped_row():
    probes = {
        "plugin_install": {"status": "ok", "detail": "x", "entries": []},
        "coexistence": {"status": "active", "detail": "x", "deferred_at": "", "reason": "", "cli_removed_marker": None},
        "statusline": {
            "status": "ok",
            "detail": "statusLine wraps your existing command",
            "ownership": "ours-wrapped",
            "command": "/p/bin/bootstrap.sh /p/bin/statusline_wrap.py",
            "wrapped_original": "bash /opt/saved.sh",
        },
        "cron": {"status": "ok", "detail": "x", "registered": True, "platform": "Darwin"},
        "venv": {"status": "ok", "detail": "x", "venv_path": "/v", "yaml_version": "6.0"},
    }
    out = doctor.render_report(probes)
    assert "--unwrap-statusline" in out
    assert "/opt/saved.sh" in out


def test_cli_wrap_statusline_writes_settings(tmp_path, monkeypatch, capsys):
    """`--wrap-statusline` end-to-end: claimed → wrapped + JSON result."""
    settings = tmp_path / "settings.json"
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash /opt/x.sh"},
    }))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))

    rc = doctor.main(["--wrap-statusline"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["action"] == "wrap-statusline"
    assert parsed["result"] == "wrapped"
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert "default-statusline-wrap-command.sh" in new_cmd


def test_cli_unwrap_statusline_round_trip(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "settings.json"
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash /opt/x.sh"},
    }))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings))

    doctor.main(["--wrap-statusline"])
    capsys.readouterr()  # discard
    rc = doctor.main(["--unwrap-statusline"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["action"] == "unwrap-statusline"
    assert parsed["result"] == "unwrapped"
    assert json.loads(settings.read_text())["statusLine"]["command"] == "bash /opt/x.sh"


def test_cli_wrap_and_unwrap_mutually_exclusive(capsys):
    rc = doctor.main(["--wrap-statusline", "--unwrap-statusline"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_cli_remove_and_wrap_mutually_exclusive(capsys):
    rc = doctor.main(["--remove-statusline", "--wrap-statusline"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_cli_force_requires_wrap_or_unwrap(capsys):
    rc = doctor.main(["--force"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--force" in err
    assert "only valid" in err
