"""End-to-end: bootstrap.sh's coexistence guard defers correctly when
CLI hooks are present in settings.json.

Pairs with coach/tests/test_coexistence_check.py (unit tests for the
Python module). This file exercises the bash-level integration —
bootstrap.sh runs coexistence_check.py, observes exit code 10, and
exits without exec-ing the wrapped Python entry.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP = REPO_ROOT / "plugin" / "bin" / "bootstrap.sh"
RUN_SH = REPO_ROOT / "plugin" / "bin" / "run.sh"
COEX_CHECK = REPO_ROOT / "plugin" / "bin" / "coexistence_check.py"
REQUIREMENTS = REPO_ROOT / "plugin" / "requirements.txt"


def _settings_with_cli_hooks() -> dict:
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


@pytest.fixture
def fake_plugin(tmp_path):
    """Plugin layout containing the real bootstrap.sh dependencies:
    bootstrap.sh, coexistence_check.py, and requirements.txt."""
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    shutil.copy2(BOOTSTRAP, root / "bin" / "bootstrap.sh")
    shutil.copy2(RUN_SH, root / "bin" / "run.sh")
    shutil.copy2(COEX_CHECK, root / "bin" / "coexistence_check.py")
    (root / "bin" / "bootstrap.sh").chmod(0o755)
    (root / "bin" / "run.sh").chmod(0o755)
    shutil.copy2(REQUIREMENTS, root / "requirements.txt")
    return root


def test_bootstrap_defers_when_cli_hooks_present(fake_plugin, tmp_path):
    """When settings.json has CLI hooks, bootstrap exits 0 WITHOUT
    exec-ing the wrapped Python entry. Proven by pointing the wrapped
    entry at a script that creates a marker file — if the marker exists
    after bootstrap returns, the entry ran (no defer)."""
    plugin_root = fake_plugin
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_cli_hooks()))
    coach_dir = tmp_path / "coach"

    marker_target = tmp_path / "wrapped_ran.flag"
    wrapped_script = tmp_path / "wrapped.py"
    wrapped_script.write_text(
        f"open({str(marker_target)!r}, 'w').write('ran')\n"
    )

    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
        "CLAUDE_SETTINGS_PATH": str(settings),
        "COACH_CONFIG_DIR": str(coach_dir),
    }
    result = subprocess.run(
        [str(plugin_root / "bin" / "bootstrap.sh"), str(wrapped_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not marker_target.exists(), (
        "bootstrap.sh should have deferred (CLI hooks present) and NOT "
        "exec'd the wrapped Python entry, but the wrapped script ran."
    )
    # Defer marker should be present
    assert (coach_dir / ".plugin-deferred").exists()


def test_bootstrap_proceeds_when_no_cli_hooks(fake_plugin, tmp_path):
    """Negative: with no CLI hooks in settings.json, bootstrap proceeds
    to exec the wrapped Python entry."""
    plugin_root = fake_plugin
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"permissions": {}}))  # no hooks
    coach_dir = tmp_path / "coach"

    marker_target = tmp_path / "wrapped_ran.flag"
    wrapped_script = tmp_path / "wrapped.py"
    wrapped_script.write_text(
        f"open({str(marker_target)!r}, 'w').write('ran')\n"
    )

    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
        "CLAUDE_SETTINGS_PATH": str(settings),
        "COACH_CONFIG_DIR": str(coach_dir),
    }
    result = subprocess.run(
        [str(plugin_root / "bin" / "bootstrap.sh"), str(wrapped_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert marker_target.exists(), (
        "bootstrap.sh should have proceeded (no CLI hooks present) and "
        "exec'd the wrapped Python entry."
    )
