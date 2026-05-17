"""plugin/bin/bootstrap.sh — venv-based PyYAML provisioning.

The bootstrap script creates a per-plugin Python venv at
${CLAUDE_PLUGIN_DATA}/venv/ on first run, installs PyYAML into it, and
execs the wrapped Python entry point under that venv. Subsequent runs
are O(diff -q) when requirements.txt is unchanged.

Two of these tests build a real Python venv (~3-5s each). They run
unconditionally — `tests/plugin/` is bundle-only and these are the
load-bearing checks for the plugin's runtime-dep story.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP = REPO_ROOT / "plugin" / "bin" / "bootstrap.sh"
RUN_SH = REPO_ROOT / "plugin" / "bin" / "run.sh"
REQUIREMENTS = REPO_ROOT / "plugin" / "requirements.txt"
PLUGIN_BIN = REPO_ROOT / "plugin" / "bin"


def _run(env_root: Path, env_data: Path, payload: str) -> subprocess.CompletedProcess:
    """Invoke bootstrap.sh with a Python -c payload as the wrapped entry."""
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(env_root),
        "CLAUDE_PLUGIN_DATA": str(env_data),
    }
    return subprocess.run(
        [str(BOOTSTRAP), "-c", payload],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def fake_plugin(tmp_path):
    """Stand up a minimal plugin layout in a tmp dir. bootstrap.sh now
    exec's into run.sh for venv setup, so the fixture needs both."""
    root = tmp_path / "plugin"
    data = tmp_path / "data"
    (root / "bin").mkdir(parents=True)
    data.mkdir()
    shutil.copy2(BOOTSTRAP, root / "bin" / "bootstrap.sh")
    shutil.copy2(RUN_SH, root / "bin" / "run.sh")
    (root / "bin" / "bootstrap.sh").chmod(0o755)
    (root / "bin" / "run.sh").chmod(0o755)
    shutil.copy2(REQUIREMENTS, root / "requirements.txt")
    return root, data


def test_bootstrap_creates_venv_and_pyyaml_importable(fake_plugin):
    root, data = fake_plugin
    result = _run(root, data, "import yaml; print('YAML_OK', yaml.__name__)")
    assert result.returncode == 0, (
        f"bootstrap exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "YAML_OK yaml" in result.stdout, result.stdout
    # venv was created
    assert (data / "venv" / "bin" / "python3").exists()
    # stamp written so next run is fast
    assert (data / "requirements.stamp").exists()
    assert (data / "requirements.stamp").read_text() == REQUIREMENTS.read_text()


def test_bootstrap_is_idempotent(fake_plugin):
    """Second invocation should NOT recreate the venv (stamp matches).
    Verified by mtime: the venv's python3 binary keeps its original mtime."""
    root, data = fake_plugin

    r1 = _run(root, data, "print('first')")
    assert r1.returncode == 0
    pybin = data / "venv" / "bin" / "python3"
    first_mtime = pybin.stat().st_mtime

    r2 = _run(root, data, "print('second')")
    assert r2.returncode == 0
    second_mtime = pybin.stat().st_mtime
    assert first_mtime == second_mtime, (
        "second bootstrap run recreated the venv — diff -q gate failed"
    )


def test_bootstrap_script_is_executable():
    """Source-of-truth in plugin/bin/ must have the executable bit set,
    or the plugin layout will fail to invoke it as a hook command."""
    mode = BOOTSTRAP.stat().st_mode
    assert mode & 0o111, (
        f"plugin/bin/bootstrap.sh is not executable (mode={oct(mode)}). "
        f"Run `chmod +x plugin/bin/bootstrap.sh`."
    )


def test_bootstrap_self_resolves_plugin_root_when_env_unset(fake_plugin):
    """bootstrap.sh must work when CLAUDE_PLUGIN_ROOT is NOT in the env.

    Claude Code injects the env var for plugin-registered hooks but NOT
    for raw `statusLine.command` strings in settings.json (where the
    plugin's statusline_self_patch writes an absolute path to
    bootstrap.sh). Without the self-resolve, exec
    "${CLAUDE_PLUGIN_ROOT}/bin/run.sh" expands to "/bin/run.sh" and
    fails with exit 126 — manifesting as a blank statusline for the
    user. Regression for v0.1.17 — DO NOT remove."""
    root, data = fake_plugin
    env = {
        **{k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"},
        "CLAUDE_PLUGIN_DATA": str(data),
    }
    env.pop("CLAUDE_PLUGIN_ROOT", None)  # explicit — fixture env may bleed
    result = subprocess.run(
        [str(root / "bin" / "bootstrap.sh"), "-c", "print('SELF_RESOLVED')"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"bootstrap.sh failed without CLAUDE_PLUGIN_ROOT in env "
        f"(rc={result.returncode})\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "SELF_RESOLVED" in result.stdout
    # And run.sh stand-alone too (same self-resolve)
    result = subprocess.run(
        [str(root / "bin" / "run.sh"), "-c", "print('RUN_SH_SELF_RESOLVED')"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"run.sh failed without CLAUDE_PLUGIN_ROOT in env "
        f"(rc={result.returncode})\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "RUN_SH_SELF_RESOLVED" in result.stdout


def test_bootstrap_falls_back_when_venv_missing(tmp_path):
    """If venv setup fails (e.g., python3 -m venv unavailable on the
    box), bootstrap should still exec system python3 so the hook's
    try/except failsafe gets a chance to run. Simulate via an empty
    CLAUDE_PLUGIN_DATA that we can't write to (read-only).

    Actually: easier reproduction — point CLAUDE_PLUGIN_ROOT at a dir
    with NO requirements.txt. bootstrap should still succeed (no venv
    needed) and fall through to system python3.
    """
    root = tmp_path / "no-reqs"
    data = tmp_path / "data"
    (root / "bin").mkdir(parents=True)
    data.mkdir()
    shutil.copy2(BOOTSTRAP, root / "bin" / "bootstrap.sh")
    shutil.copy2(RUN_SH, root / "bin" / "run.sh")
    (root / "bin" / "bootstrap.sh").chmod(0o755)
    (root / "bin" / "run.sh").chmod(0o755)
    # NO requirements.txt — diff -q will fail, venv attempt will also
    # likely fail because pip install -r <missing> errors. Bootstrap
    # delegates to run.sh which falls through to system python3.

    result = _run(root, data, "print('SYSTEM_PY')")
    assert result.returncode == 0, (
        f"bootstrap should fall through to system python3 even without "
        f"requirements.txt; got rc={result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "SYSTEM_PY" in result.stdout
