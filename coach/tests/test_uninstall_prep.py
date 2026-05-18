"""uninstall_prep: pre-uninstall cleanup + UserPromptSubmit intercept.

Claude Code's /plugin uninstall is non-extensible — no lifecycle hooks.
v0.1.20 closes the gap with a two-piece mechanism:

  1. coach-user-prompt.py intercepts `/plugin uninstall
     coach-claw@coach-claw-plugins` (under CLAUDE_PLUGIN_ROOT) when the
     ~/.claude/coach/.uninstall-prepped marker is absent, exits 2 with a
     stderr message pointing the user at the prep skill.
  2. doctor.py --uninstall-prep clears statusLine and writes the marker.
     With --wipe-data, also archives profile data via mv (not rm).

These tests pin both pieces.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    """Load coach-user-prompt.py."""
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_uninstall_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def doctor():
    """Load doctor.py."""
    repo_path = Path(__file__).resolve().parents[1] / "bin" / "doctor.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "coach" / "bin" / "doctor.py"
    if not path.exists():
        pytest.skip(f"doctor.py not at {path}")
    # doctor.py imports from same dir — set sys.path
    import sys as _sys
    _sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("doctor_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Intercept-pattern detection
# ---------------------------------------------------------------------------


def test_intercept_matches_canonical_form(cup):
    """Exact canonical command form triggers the intercept."""
    assert cup._is_coach_plugin_uninstall("/plugin uninstall coach-claw@coach-claw-plugins")


def test_intercept_matches_with_surrounding_whitespace(cup):
    """Leading/trailing whitespace doesn't defeat the match."""
    assert cup._is_coach_plugin_uninstall("  /plugin uninstall coach-claw@coach-claw-plugins  \n")


def test_intercept_does_not_match_short_form(cup):
    """Bare `coach-claw` (no marketplace suffix) is NOT intercepted.
    Conservative — users who type the short form trade the warning for
    speed."""
    assert not cup._is_coach_plugin_uninstall("/plugin uninstall coach-claw")


def test_intercept_does_not_match_unrelated_commands(cup):
    """Random prompts are not intercepted."""
    assert not cup._is_coach_plugin_uninstall("hello world")
    assert not cup._is_coach_plugin_uninstall("/plugin install coach-claw@coach-claw-plugins")
    assert not cup._is_coach_plugin_uninstall("/plugin uninstall some-other-plugin")
    assert not cup._is_coach_plugin_uninstall("")


def test_intercept_message_includes_both_prep_options(cup):
    """Stderr message names both default and --wipe-data variants."""
    msg = cup._uninstall_intercept_message()
    assert "/coach-claw:doctor --uninstall-prep" in msg
    assert "--wipe-data" in msg
    assert "preserves" in msg.lower() or "preserve" in msg.lower()
    assert "/plugin uninstall coach-claw@coach-claw-plugins" in msg


# ---------------------------------------------------------------------------
# doctor.uninstall_prep — default (preserve profile)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Synthesize ~/.claude/coach/, settings.json, plugin cache."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("COACH_CONFIG_DIR", str(home / ".claude" / "coach"))
    coach_dir = home / ".claude" / "coach"
    coach_dir.mkdir(parents=True)
    (coach_dir / "profile.yaml").write_text("schema_version: 1\n")
    (coach_dir / "banked_sessions.json").write_text("{}")
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {
            "type": "command",
            "command": "/fake/plugin/bin/run.sh /fake/plugin/bin/default_statusline.py",
        },
    }))
    return home, coach_dir, settings


def test_uninstall_prep_default_preserves_profile(doctor, fake_env, monkeypatch):
    home, coach_dir, settings = fake_env
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    # Also patch resolve_coach_dir to read our COACH_CONFIG_DIR
    monkeypatch.setattr(doctor, "resolve_coach_dir", lambda: coach_dir)

    result = doctor.uninstall_prep(wipe_data=False)
    assert result["result"] == "prepped"
    assert result["wipe_data"] is False
    # Profile preserved
    assert (coach_dir / "profile.yaml").exists()
    assert (coach_dir / "banked_sessions.json").exists()
    # Marker written
    marker = coach_dir / ".uninstall-prepped"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["wipe_data"] is False
    assert payload["archived_to"] is None
    # No archive dir created
    assert not list(home.glob(".claude/coach.bak.*"))


def test_uninstall_prep_wipe_data_archives_not_deletes(doctor, fake_env, monkeypatch):
    home, coach_dir, settings = fake_env
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    monkeypatch.setattr(doctor, "resolve_coach_dir", lambda: coach_dir)
    # Write a sentinel to confirm the file moves to bak (not rm)
    (coach_dir / "profile.yaml").write_text("schema_version: 1\nsentinel: 42\n")

    result = doctor.uninstall_prep(wipe_data=True)
    assert result["result"] == "prepped"
    assert result["wipe_data"] is True
    assert result["archived_to"] is not None

    archive_path = Path(result["archived_to"])
    assert archive_path.exists()
    assert archive_path.name.startswith("coach.bak.")
    # Sentinel survived the move (reversible — not rm'd)
    sentinel = (archive_path / "profile.yaml").read_text()
    assert "sentinel: 42" in sentinel

    # New empty coach_dir recreated for the marker
    assert coach_dir.exists()
    assert (coach_dir / ".uninstall-prepped").exists()


def test_uninstall_prep_writes_marker_intercept_will_recognize(doctor, fake_env, monkeypatch, cup):
    """End-to-end: prep writes the marker, intercept-side gate checks
    for the SAME marker path. Pin the contract."""
    home, coach_dir, settings = fake_env
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    monkeypatch.setattr(doctor, "resolve_coach_dir", lambda: coach_dir)

    doctor.uninstall_prep(wipe_data=False)
    marker = coach_dir / ".uninstall-prepped"
    assert marker.exists()
    # And the intercept's COACH_DIR resolution must match this same path
    # when COACH_CONFIG_DIR is set (the env we set in fake_env). Already
    # part of cup module-level COACH_DIR contract.
    expected = Path(os.environ["COACH_CONFIG_DIR"]) / ".uninstall-prepped"
    assert marker == expected


# ---------------------------------------------------------------------------
# v0.1.23: abort marker write when statusLine cleanup actually fails.
# ---------------------------------------------------------------------------


def test_uninstall_prep_aborts_on_statusline_error(doctor, fake_env, monkeypatch):
    """If remove_statusline() returns "error" the marker must NOT be
    written. Writing it on a failed cleanup lets the next /plugin
    uninstall silently bypass the v0.1.20 intercept while a Coach
    statusLine is still in settings.json — exactly the orphan state
    the intercept was meant to prevent."""
    _, coach_dir, settings = fake_env
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    monkeypatch.setattr(doctor, "resolve_coach_dir", lambda: coach_dir)
    monkeypatch.setattr(doctor, "remove_statusline", lambda: {
        "action": "remove-statusline",
        "result": "error",
        "detail": "synthetic test failure",
    })

    result = doctor.uninstall_prep(wipe_data=False)
    assert result["result"] == "error"
    assert "synthetic test failure" in result["detail"]
    assert not (coach_dir / ".uninstall-prepped").exists(), (
        "marker must NOT exist after a failed cleanup"
    )


def test_uninstall_prep_writes_marker_when_statusline_skipped(doctor, fake_env, monkeypatch):
    """`skipped` (statusLine points at a non-Coach command — claimed or
    integrated-externally) is a safe outcome: uninstall won't leave a
    Coach orphan. Marker is written."""
    _, coach_dir, settings = fake_env
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    monkeypatch.setattr(doctor, "resolve_coach_dir", lambda: coach_dir)
    monkeypatch.setattr(doctor, "remove_statusline", lambda: {
        "action": "remove-statusline",
        "result": "skipped",
        "detail": "statusLine points at a non-Coach command; left untouched.",
    })

    result = doctor.uninstall_prep(wipe_data=False)
    assert result["result"] == "prepped"
    assert (coach_dir / ".uninstall-prepped").exists()


def test_uninstall_prep_writes_marker_when_no_op(doctor, fake_env, monkeypatch):
    """`no-op` (no settings.json or no statusLine key) is a safe outcome.
    Marker is written."""
    _, coach_dir, settings = fake_env
    monkeypatch.setattr(doctor, "SETTINGS_PATH", settings)
    monkeypatch.setattr(doctor, "resolve_coach_dir", lambda: coach_dir)
    monkeypatch.setattr(doctor, "remove_statusline", lambda: {
        "action": "remove-statusline",
        "result": "no-op",
        "detail": "no settings.json to modify",
    })

    result = doctor.uninstall_prep(wipe_data=False)
    assert result["result"] == "prepped"
    assert (coach_dir / ".uninstall-prepped").exists()
