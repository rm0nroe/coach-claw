"""Regression: hooks must use the bin/ that ships WITH them.

Discovered during e2e validation (2026-05-09): the plugin's hooks were
putting `~/.claude/coach/bin/` (the CLI's install dir) on sys.path
instead of `${CLAUDE_PLUGIN_ROOT}/bin/`. When a user had the npm CLI
installed at an older version that pre-dated newer plugin-track
modules (cron_check, statusline_self_patch, etc.), the plugin's hook
would silently fall back to the CLI's stale modules — and the imports
inside `_maybe_install_plugin_statusline` and
`_maybe_cron_nudge_block` would fail with `ModuleNotFoundError`,
suppressed by the failsafe try/except.

Net effect: the plugin's hook fired, but the new plugin-track
behaviors silently no-op'd. statusLine never self-installed. Cron
nudge never appeared. No error. No log line.

Both hooks now branch on `CLAUDE_PLUGIN_ROOT`: if set, prefer the
plugin's bin/; otherwise use the CLI's. This test pins that branch
by importing each hook under controlled env vars and inspecting
which path landed at `sys.path[0]`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_START_HOOK = REPO_ROOT / "hooks" / "coach-session-start.py"
USER_PROMPT_HOOK = REPO_ROOT / "hooks" / "coach-user-prompt.py"


def _import_isolated(path: Path, env_vars: dict[str, str], monkeypatch):
    """Load a hook module with monkeypatched env vars + isolated sys.path.

    Returns sys.path BEFORE the hook ran (saved snapshot) plus the
    paths the hook prepended (everything new at the front of sys.path).
    """
    saved = list(sys.path)
    # Drop any previous hook import cached in sys.modules so module-load
    # side effects re-execute under the new env.
    for mod_name in list(sys.modules):
        if "cup_under_test" in mod_name or "css_under_test" in mod_name:
            sys.modules.pop(mod_name)

    for k in ("CLAUDE_PLUGIN_ROOT", "COACH_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)

    name = "css_under_test" if "session-start" in path.name else "cup_under_test"
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Anything in sys.path that wasn't there before are the prepends.
    new_paths = [p for p in sys.path if p not in saved]
    return new_paths, mod


@pytest.fixture
def fake_plugin(tmp_path):
    """Two parallel bin dirs in tmp: a 'plugin' bin and a 'cli' bin.
    Tests verify which one lands on sys.path."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "bin").mkdir(parents=True)
    coach_dir = tmp_path / "coach"
    (coach_dir / "bin").mkdir(parents=True)
    return plugin_root, coach_dir


@pytest.mark.parametrize("hook_path", [SESSION_START_HOOK, USER_PROMPT_HOOK])
def test_plugin_context_uses_plugin_bin(hook_path, fake_plugin, monkeypatch):
    plugin_root, coach_dir = fake_plugin
    new_paths, _ = _import_isolated(
        hook_path,
        {
            "CLAUDE_PLUGIN_ROOT": str(plugin_root),
            "COACH_CONFIG_DIR": str(coach_dir),
        },
        monkeypatch,
    )
    plugin_bin = str(plugin_root / "bin")
    cli_bin = str(coach_dir / "bin")
    # plugin bin should be the FIRST insertion (sys.path[0])
    assert new_paths and new_paths[0] == plugin_bin, (
        f"With CLAUDE_PLUGIN_ROOT set, hook must put ${{CLAUDE_PLUGIN_ROOT}}/bin/ "
        f"on sys.path. Expected first prepend = {plugin_bin!r}; got new paths = "
        f"{new_paths!r}"
    )
    # CLI bin must NOT be on sys.path in plugin context — using stale CLI
    # modules is the bug this test pins.
    assert cli_bin not in sys.path, (
        f"CLI bin {cli_bin!r} should NOT be on sys.path under plugin context. "
        f"Got sys.path entries: {[p for p in sys.path if 'bin' in p]!r}"
    )


@pytest.mark.parametrize("hook_path", [SESSION_START_HOOK, USER_PROMPT_HOOK])
def test_cli_context_uses_coach_bin(hook_path, fake_plugin, monkeypatch):
    """Without CLAUDE_PLUGIN_ROOT, hooks fall back to ${COACH_DIR}/bin/
    (the CLI install layout)."""
    _, coach_dir = fake_plugin
    new_paths, _ = _import_isolated(
        hook_path,
        {"COACH_CONFIG_DIR": str(coach_dir)},
        monkeypatch,
    )
    cli_bin = str(coach_dir / "bin")
    assert new_paths and new_paths[0] == cli_bin, (
        f"Without CLAUDE_PLUGIN_ROOT, hook must put ${{COACH_CONFIG_DIR}}/bin/ "
        f"on sys.path. Expected first prepend = {cli_bin!r}; got new paths = "
        f"{new_paths!r}"
    )
