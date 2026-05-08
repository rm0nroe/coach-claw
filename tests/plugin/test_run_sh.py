"""plugin/bin/run.sh — skill-invocation wrapper.

Same venv-or-system Python resolution as bootstrap.sh, but WITHOUT
the coexistence guard. Skills are user-invoked and explicit; they
should always run, never defer.

Pinned by these tests:
- venv gets created on first invocation (and PyYAML installed)
- subsequent invocations are O(diff -q) — no rebuild
- run.sh does NOT defer when CLI hooks are present in settings.json
  (this is the key behavior difference vs. bootstrap.sh)
- run.sh falls back to system python3 if venv setup fails (no requirements.txt)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_SH = REPO_ROOT / "plugin" / "bin" / "run.sh"
COEX = REPO_ROOT / "plugin" / "bin" / "coexistence_check.py"
REQUIREMENTS = REPO_ROOT / "plugin" / "requirements.txt"


@pytest.fixture
def fake_plugin(tmp_path):
    """Stand up a minimal plugin layout including coexistence_check.py
    so we can prove run.sh ignores it."""
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    shutil.copy2(RUN_SH, root / "bin" / "run.sh")
    shutil.copy2(COEX, root / "bin" / "coexistence_check.py")
    (root / "bin" / "run.sh").chmod(0o755)
    shutil.copy2(REQUIREMENTS, root / "requirements.txt")
    return root


def _run(plugin_root: Path, data_dir: Path, env_extra: dict, payload: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_DATA": str(data_dir),
        **env_extra,
    }
    return subprocess.run(
        [str(plugin_root / "bin" / "run.sh"), "-c", payload],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_run_sh_creates_venv_and_pyyaml_works(fake_plugin, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    result = _run(fake_plugin, data, {}, "import yaml; print('YAML_OK', yaml.__name__)")
    assert result.returncode == 0, result.stderr
    assert "YAML_OK yaml" in result.stdout
    assert (data / "venv" / "bin" / "python3").exists()
    assert (data / "requirements.stamp").exists()


def test_run_sh_is_idempotent(fake_plugin, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    r1 = _run(fake_plugin, data, {}, "print('first')")
    assert r1.returncode == 0
    pybin = data / "venv" / "bin" / "python3"
    first_mtime = pybin.stat().st_mtime
    r2 = _run(fake_plugin, data, {}, "print('second')")
    assert r2.returncode == 0
    assert pybin.stat().st_mtime == first_mtime


def test_run_sh_does_NOT_defer_when_cli_hooks_present(fake_plugin, tmp_path):
    """Critical contract: run.sh must NOT consult coexistence_check.
    Even with CLI hooks registered in settings.json, slash commands
    (which route through run.sh) must execute.

    Verified by writing a settings.json with CLI-style hook entries,
    pointing CLAUDE_SETTINGS_PATH at it, then asserting run.sh's
    payload actually executed (vs. silently exiting like bootstrap.sh
    would).
    """
    data = tmp_path / "data"
    data.mkdir()
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
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
    }))
    # CLAUDE_SETTINGS_PATH is the override coexistence_check.py honors.
    # If run.sh accidentally runs the coexistence check, the script
    # would exit early and "RAN" would NOT appear in stdout.
    result = _run(
        fake_plugin, data,
        {"CLAUDE_SETTINGS_PATH": str(settings)},
        "print('RAN')",
    )
    assert result.returncode == 0, result.stderr
    assert "RAN" in result.stdout, (
        f"run.sh appears to have deferred (CLI hooks present): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_run_sh_falls_back_to_system_python_when_venv_setup_fails(tmp_path):
    """If requirements.txt is absent, venv setup fails (pip can't
    install from a missing file). run.sh must still exec system
    python3 so the wrapped script gets a chance to run."""
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    shutil.copy2(RUN_SH, root / "bin" / "run.sh")
    (root / "bin" / "run.sh").chmod(0o755)
    # NO requirements.txt — venv setup will fail.

    data = tmp_path / "data"
    data.mkdir()
    result = subprocess.run(
        [str(root / "bin" / "run.sh"), "-c", "print('SYSTEM_PY')"],
        env={
            **os.environ,
            "CLAUDE_PLUGIN_ROOT": str(root),
            "CLAUDE_PLUGIN_DATA": str(data),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "SYSTEM_PY" in result.stdout


def test_run_sh_is_executable():
    mode = RUN_SH.stat().st_mode
    assert mode & 0o111, (
        f"plugin/bin/run.sh is not executable (mode={oct(mode)})"
    )
