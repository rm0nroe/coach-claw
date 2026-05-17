"""statusline_wrap_action — wrap/unwrap idempotency, opt-out stickiness,
path freshness, current-command guard, manual-Coach pre-flight, and the
`wrap-if-claimed` CLI subcommand."""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

import statusline_wrap_action as wa


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def settings_path(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({}))
    return p


@pytest.fixture
def coach_dir(tmp_path):
    p = tmp_path / "coach"
    p.mkdir()
    return p


@pytest.fixture
def plugin_root(tmp_path):
    p = tmp_path / "plugin"
    (p / "bin").mkdir(parents=True)
    return p


def _set_statusline(settings_path: Path, command: str) -> None:
    data = json.loads(settings_path.read_text())
    data["statusLine"] = {"type": "command", "command": command}
    settings_path.write_text(json.dumps(data))


def _read_statusline(settings_path: Path) -> str:
    return json.loads(settings_path.read_text())["statusLine"]["command"]


# --- wrap() happy paths ---------------------------------------------------


def test_wrap_on_claimed_writes_marker_and_mutates_settings(
    settings_path, coach_dir
):
    _set_statusline(settings_path, "bash /opt/my-line.sh")
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)

    assert res["result"] == "wrapped"
    marker = json.loads((coach_dir / wa.WRAP_MARKER_NAME).read_text())
    assert marker["original_command"] == "bash /opt/my-line.sh"

    new_cmd = _read_statusline(settings_path)
    assert "default-statusline-wrap-command.sh" in new_cmd  # CLI shape


def test_wrap_with_plugin_root_uses_trampoline_shape(
    settings_path, coach_dir, plugin_root
):
    """v0.1.22+: plugin shape routes through the stable trampoline under
    coach_dir, NOT a versioned plugin_root path. Trampoline + .plugin-root
    cache are written as a side-effect."""
    _set_statusline(settings_path, "bash /opt/my-line.sh")
    res = wa.wrap(
        coach_dir=coach_dir,
        settings_path=settings_path,
        plugin_root=plugin_root,
    )
    assert res["result"] == "wrapped"
    new_cmd = _read_statusline(settings_path)
    assert "plugin-statusline.sh" in new_cmd
    assert "statusline_wrap.py" in new_cmd
    # plugin_root must NOT appear in settings.json — that's the bug we fixed.
    assert str(plugin_root) not in new_cmd
    assert "default-statusline-wrap-command.sh" not in new_cmd
    # .plugin-root cache populated.
    cache = (coach_dir / ".plugin-root").read_text().strip()
    assert cache == str(plugin_root.resolve())


def test_wrap_writes_announce_marker(settings_path, coach_dir):
    _set_statusline(settings_path, "bash /opt/x.sh")
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert (coach_dir / wa.ANNOUNCE_MARKER_NAME).exists()


def test_wrap_clears_existing_optout_marker(settings_path, coach_dir):
    _set_statusline(settings_path, "bash /opt/x.sh")
    (coach_dir / wa.DISABLED_MARKER_NAME).write_text(json.dumps({"reason": "stale"}))
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path, force=True)
    assert not (coach_dir / wa.DISABLED_MARKER_NAME).exists()


# --- wrap() idempotency / skip paths --------------------------------------


def test_wrap_idempotent_when_already_wrapped(
    settings_path, coach_dir, plugin_root
):
    _set_statusline(settings_path, "bash /opt/x.sh")
    wa.wrap(
        coach_dir=coach_dir, settings_path=settings_path,
        plugin_root=plugin_root,
    )
    cmd_after_first = _read_statusline(settings_path)

    res2 = wa.wrap(
        coach_dir=coach_dir, settings_path=settings_path,
        plugin_root=plugin_root,
    )
    assert res2["result"] == "no-op"
    assert _read_statusline(settings_path) == cmd_after_first


def test_wrap_refreshes_stale_plugin_root_in_cache(
    settings_path, coach_dir, tmp_path
):
    """v0.1.22+: ours-wrapped with a different plugin_root updates the
    .plugin-root cache (so the trampoline resolves to the new dir on
    next exec) but settings.json command stays stable (the trampoline
    path under coach_dir doesn't change across plugin versions)."""
    old_root = tmp_path / "plugin-old"
    (old_root / "bin").mkdir(parents=True)
    new_root = tmp_path / "plugin-new"
    (new_root / "bin").mkdir(parents=True)

    _set_statusline(settings_path, "bash /opt/x.sh")
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path, plugin_root=old_root)
    cmd_after_first = _read_statusline(settings_path)
    assert (coach_dir / ".plugin-root").read_text().strip() == str(old_root.resolve())

    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path, plugin_root=new_root)
    # No settings.json mutation needed — the trampoline command is stable
    # across plugin updates; only the cache file moves.
    assert res["result"] == "no-op"
    assert res["reason"] == "already-wrapped"
    assert _read_statusline(settings_path) == cmd_after_first
    # Cache refreshed in place.
    assert (coach_dir / ".plugin-root").read_text().strip() == str(new_root.resolve())


def test_wrap_migrates_legacy_versioned_wrap_command(
    settings_path, coach_dir, tmp_path, plugin_root
):
    """A pre-v0.1.22 wrap command (`<plugin>/bin/run.sh
    <plugin>/bin/statusline_wrap.py`, versioned) must be migrated to
    the stable trampoline shape on encounter — this is the load-bearing
    fix for the recurring 'Plugin directory does not exist: .../<old>'
    error that fires when a long-running CC session holds a stale
    CLAUDE_PLUGIN_ROOT after /plugin update."""
    old_root = tmp_path / "plugin-OLD-version"
    (old_root / "bin").mkdir(parents=True)
    _set_statusline(
        settings_path,
        f"{old_root}/bin/run.sh {old_root}/bin/statusline_wrap.py",
    )
    # Pre-existing wrap marker — required for the action to recognize
    # the entry as ours-wrapped.
    (coach_dir / wa.WRAP_MARKER_NAME).write_text(json.dumps({
        "original_command": "bash /opt/x.sh",
    }))

    res = wa.wrap(
        coach_dir=coach_dir, settings_path=settings_path, plugin_root=plugin_root
    )
    assert res["result"] == "wrapped"
    assert res["reason"] == "refreshed-path"
    new_cmd = _read_statusline(settings_path)
    assert "plugin-statusline.sh" in new_cmd
    assert "statusline_wrap.py" in new_cmd
    assert str(old_root) not in new_cmd


def test_wrap_respects_optout_marker(settings_path, coach_dir):
    """Sticky opt-out → wrap skips and does not mutate settings."""
    _set_statusline(settings_path, "bash /opt/x.sh")
    (coach_dir / wa.DISABLED_MARKER_NAME).write_text(json.dumps({
        "reason": "user-unwrapped",
    }))
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "skipped"
    assert res["reason"] == "opted-out"
    assert _read_statusline(settings_path) == "bash /opt/x.sh"  # untouched


def test_wrap_force_bypasses_optout(settings_path, coach_dir):
    _set_statusline(settings_path, "bash /opt/x.sh")
    (coach_dir / wa.DISABLED_MARKER_NAME).write_text(json.dumps({
        "reason": "user-unwrapped",
    }))
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path, force=True)
    assert res["result"] == "wrapped"


def test_wrap_skips_when_settings_absent(coach_dir, tmp_path):
    no_settings = tmp_path / "nope.json"
    res = wa.wrap(coach_dir=coach_dir, settings_path=no_settings)
    assert res["result"] == "skipped"
    assert res["reason"] == "no-settings"


def test_wrap_skips_when_statusline_absent(settings_path, coach_dir):
    """Empty settings.json → no statusLine → wrap does nothing."""
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "skipped"
    assert res["reason"] == "absent"


# --- manual-Coach pre-flight ----------------------------------------------


def test_wrap_skips_when_user_script_references_stats_py(
    settings_path, coach_dir, tmp_path
):
    """Ryan's exact case: user's statusline-command.sh internally calls
    coach/bin/stats.py → wrap must detect and skip."""
    user_script = tmp_path / "statusline-command.sh"
    user_script.write_text(
        "#!/bin/bash\n"
        'exec "$HOME/.claude/coach/bin/stats.py"\n'
    )
    _set_statusline(settings_path, f"bash {user_script}")

    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "skipped"
    assert res["reason"] == "already-integrated"
    assert str(user_script) in res.get("detected_in", "")
    # Settings.json untouched
    assert _read_statusline(settings_path) == f"bash {user_script}"
    # Sticky opt-out marker written so future auto-wrap also skips.
    disabled = json.loads((coach_dir / wa.DISABLED_MARKER_NAME).read_text())
    assert disabled["reason"] == "already-integrated"


def test_wrap_skips_when_user_script_references_default_statusline(
    settings_path, coach_dir, tmp_path
):
    user_script = tmp_path / "my-line.sh"
    user_script.write_text(
        "#!/bin/bash\n"
        '"$HOME/.claude/coach/bin/default_statusline.py"\n'
    )
    _set_statusline(settings_path, f"bash {user_script}")
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "skipped"
    assert res["reason"] == "already-integrated"


def test_wrap_proceeds_on_unrelated_user_script(settings_path, coach_dir, tmp_path):
    """User's script doesn't reference Coach internals → wrap proceeds."""
    user_script = tmp_path / "other.sh"
    user_script.write_text("#!/bin/bash\necho 'just a custom statusline'\n")
    _set_statusline(settings_path, f"bash {user_script}")

    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "wrapped"


def test_wrap_proceeds_when_command_has_no_script_file(settings_path, coach_dir):
    """`bash -c '...'` shape — static sniff can't introspect; fall through
    and let runtime duplicate-detection handle it."""
    _set_statusline(settings_path, "bash -c 'echo hi'")
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "wrapped"


def test_wrap_force_bypasses_manual_coach_pre_flight(
    settings_path, coach_dir, tmp_path
):
    """`--force` proceeds even when manual integration is detected."""
    user_script = tmp_path / "statusline-command.sh"
    user_script.write_text(
        "#!/bin/bash\nexec coach/bin/stats.py\n"
    )
    _set_statusline(settings_path, f"bash {user_script}")
    res = wa.wrap(coach_dir=coach_dir, settings_path=settings_path, force=True)
    assert res["result"] == "wrapped"


# --- unwrap() happy paths -------------------------------------------------


def test_unwrap_round_trip_restores_original(settings_path, coach_dir):
    original = "bash /opt/my-original.sh"
    _set_statusline(settings_path, original)
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path)

    res = wa.unwrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "unwrapped"
    assert _read_statusline(settings_path) == original
    # Marker deleted
    assert not (coach_dir / wa.WRAP_MARKER_NAME).exists()
    # Sticky opt-out written
    disabled = json.loads((coach_dir / wa.DISABLED_MARKER_NAME).read_text())
    assert disabled["reason"] == "user-unwrapped"


def test_unwrap_no_op_when_no_marker(settings_path, coach_dir):
    res = wa.unwrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "no-op"
    assert res["reason"] == "no-wrap-marker"


def test_unwrap_refused_when_command_changed_since_wrap(
    settings_path, coach_dir
):
    """User manually edited settings.json:statusLine after wrap → unwrap
    refuses to clobber the manual edit."""
    _set_statusline(settings_path, "bash /opt/x.sh")
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    # User overrides statusLine manually
    _set_statusline(settings_path, "bash /something/else.sh")

    res = wa.unwrap(coach_dir=coach_dir, settings_path=settings_path)
    assert res["result"] == "refused"
    assert res["reason"] == "command-changed-since-wrap"
    # Manual edit preserved
    assert _read_statusline(settings_path) == "bash /something/else.sh"


def test_unwrap_force_clobbers_manual_edit(settings_path, coach_dir):
    _set_statusline(settings_path, "bash /opt/x.sh")
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    _set_statusline(settings_path, "bash /something/else.sh")

    res = wa.unwrap(coach_dir=coach_dir, settings_path=settings_path, force=True)
    assert res["result"] == "unwrapped"
    assert _read_statusline(settings_path) == "bash /opt/x.sh"


def test_wrap_after_unwrap_clears_optout_for_re_optin(settings_path, coach_dir):
    _set_statusline(settings_path, "bash /opt/x.sh")
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path)
    wa.unwrap(coach_dir=coach_dir, settings_path=settings_path)
    # opt-out marker present
    assert (coach_dir / wa.DISABLED_MARKER_NAME).exists()

    # Explicit wrap with force re-opts in (clears marker).
    wa.wrap(coach_dir=coach_dir, settings_path=settings_path, force=True)
    assert not (coach_dir / wa.DISABLED_MARKER_NAME).exists()


# --- custom-paths contract (fix #3) ---------------------------------------


def test_explicit_paths_isolate_writes(tmp_path):
    """No `Path.home()` calls leak through — markers land in coach_dir,
    settings mutation writes to settings_path, even when both are
    arbitrary tmp paths."""
    custom_coach = tmp_path / "x"
    custom_coach.mkdir()
    custom_settings = tmp_path / "y" / "settings.json"
    custom_settings.parent.mkdir()
    custom_settings.write_text(json.dumps({"statusLine": {"command": "bash /a.sh"}}))

    res = wa.wrap(coach_dir=custom_coach, settings_path=custom_settings)
    assert res["result"] == "wrapped"
    # Markers in custom_coach, NOT in $HOME
    assert (custom_coach / wa.WRAP_MARKER_NAME).exists()
    # Settings.json mutated at custom path
    assert "default-statusline-wrap-command.sh" in _read_statusline(custom_settings)


# --- CLI: wrap-if-claimed --------------------------------------------------


def test_wrap_if_claimed_cli_uses_env_settings_path(
    settings_path, coach_dir, monkeypatch, capsys
):
    """install.sh sets COACH_CONFIG_DIR + CLAUDE_SETTINGS_PATH; the CLI
    subcommand honors both."""
    _set_statusline(settings_path, "bash /opt/x.sh")
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings_path))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

    rc = wa.main(["wrap-if-claimed"])
    assert rc == 0  # never breaks an install
    out = capsys.readouterr().out
    assert "wrapped" in out.lower()
    # CLI shape used (no plugin root in env)
    assert "default-statusline-wrap-command.sh" in _read_statusline(settings_path)


def test_wrap_if_claimed_cli_with_plugin_root_uses_trampoline_shape(
    settings_path, coach_dir, plugin_root, monkeypatch
):
    """v0.1.22+: CLAUDE_PLUGIN_ROOT in env → wrap routes through the
    stable trampoline path under coach_dir, NOT a versioned plugin
    path."""
    _set_statusline(settings_path, "bash /opt/x.sh")
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

    rc = wa.main(["wrap-if-claimed"])
    assert rc == 0
    cmd = _read_statusline(settings_path)
    assert "plugin-statusline.sh" in cmd
    assert "statusline_wrap.py" in cmd
    assert str(plugin_root) not in cmd


def test_wrap_if_claimed_cli_exits_zero_on_no_settings(
    coach_dir, tmp_path, monkeypatch
):
    """CLI must NEVER fail-hard; install.sh expects exit 0."""
    monkeypatch.setenv("COACH_CONFIG_DIR", str(coach_dir))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(tmp_path / "missing.json"))
    rc = wa.main(["wrap-if-claimed"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Shell-safety of generated wrapper commands (v0.1.5 fix)
# ---------------------------------------------------------------------------


def test_build_wrapper_command_quotes_cli_paths_with_spaces(tmp_path):
    """CLI shape: a coach_dir containing spaces must produce a command
    string that bash parses as ONE token for the trampoline path. Pre-
    v0.1.5 this generated `bash /tmp/.../Claude Dir/coach/...` which
    bash split into `bash /tmp/.../Claude` + `Dir/coach/...`, ENOENT'ing
    the second token."""
    coach_dir = tmp_path / "Claude Dir With Spaces" / "coach"
    coach_dir.mkdir(parents=True)
    cmd = wa._build_wrapper_command(coach_dir=coach_dir, plugin_root=None)

    tokens = shlex.split(cmd)
    assert tokens[0] == "bash"
    assert len(tokens) == 2, (
        f"trampoline path was split by bash; tokens={tokens!r} cmd={cmd!r}"
    )
    assert tokens[1] == str(coach_dir / "default-statusline-wrap-command.sh")


def test_build_wrapper_command_quotes_plugin_paths_with_spaces(tmp_path):
    """Plugin shape (v0.1.22+): trampoline lives under coach_dir, so
    space-protection applies to the coach_dir path. plugin_root no
    longer appears in the command at all (it lives in .plugin-root
    cache instead)."""
    coach_dir = tmp_path / "Coach Dir With Spaces"
    plugin_root = tmp_path / "Plugin Dir With Spaces"
    (plugin_root / "bin").mkdir(parents=True)
    cmd = wa._build_wrapper_command(coach_dir=coach_dir, plugin_root=plugin_root)

    tokens = shlex.split(cmd)
    assert tokens == [
        "bash",
        str(coach_dir / "plugin-statusline.sh"),
        "statusline_wrap.py",
    ]
    assert str(plugin_root) not in cmd
