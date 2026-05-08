"""Regression test for the PyYAML preflight fallback chain in install.sh.

A previous round of the installer recommended `brew install pyyaml` as
the Homebrew-blessed recovery path. The formula does not exist in
Homebrew core (`brew info pyyaml` → `Error: No available formula`). The
strategy and the error message that included it were dead code — the
recovery instructions pointed users at a command that fails.

These tests pin the current shape:

  • install.sh does not mention `brew install pyyaml` anywhere in source
  • when both legitimate pip strategies fail, the recovery message
    surfaces only real options (`pip --break-system-packages` and venv)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _git_env() -> dict:
    return {
        "GIT_AUTHOR_NAME": "Coach Tests",
        "GIT_AUTHOR_EMAIL": "coach-tests@example.invalid",
        "GIT_COMMITTER_NAME": "Coach Tests",
        "GIT_COMMITTER_EMAIL": "coach-tests@example.invalid",
    }


def test_install_sh_does_not_reference_dead_brew_pyyaml() -> None:
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    src = (repo / "install.sh").read_text()
    assert "brew install pyyaml" not in src, (
        "install.sh references `brew install pyyaml`, but no such "
        "formula exists in Homebrew core. Use `pip install --user "
        "--break-system-packages pyyaml` or a venv instead."
    )


def test_install_pyyaml_fallback_message_excludes_dead_brew_path(
    tmp_path: Path,
) -> None:
    """Run install.sh with python3 PATH-shimmed to simulate missing
    PyYAML and refusing pip. The installer must fail with exit != 0
    and an error message that:

      • shows BOTH legitimate strategies were tried (pip --user, then
        pip --user --break-system-packages),
      • offers --break-system-packages and venv as manual recovery,
      • does NOT mention `brew install pyyaml` or claim Homebrew Python
        gets a special blessed strategy.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    real_python3 = shutil.which("python3")
    assert real_python3, "real python3 must be on PATH for the shim to delegate"

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    # Bash shim: blocks `-c "import yaml"` and `-m pip ...` by exiting 1;
    # delegates everything else (version check via heredoc, etc.) to the
    # real python3.
    shim.write_text(
        """#!/bin/bash
case "$1" in
  -c)
    case "$2" in
      *"import yaml"*) exit 1 ;;
    esac
    ;;
  -m)
    case "$2" in
      pip) exit 1 ;;
    esac
    ;;
esac
exec "$COACH_REAL_PYTHON3" "$@"
"""
    )
    shim.chmod(0o755)

    claude_dir = tmp_path / "claude_dir"
    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_DIR": str(claude_dir),
            "PATH": f"{shim_dir}{os.pathsep}{env['PATH']}",
            "COACH_REAL_PYTHON3": real_python3,
            **_git_env(),
        }
    )

    result = subprocess.run(
        ["bash", str(repo / "install.sh")],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    out = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"installer succeeded with pip stubbed to fail; "
        f"preflight should have aborted:\n{out}"
    )

    # Both legitimate strategies must have been attempted before the
    # error message fires.
    assert "pip install --user pyyaml" in out, (
        f"strategy 1 (pip --user) was not attempted:\n{out}"
    )
    assert "pip install --user --break-system-packages pyyaml" in out, (
        f"strategy 2 (pip --break-system-packages) was not attempted:\n{out}"
    )

    # Dead Homebrew path must not appear anywhere — neither as a strategy
    # the installer claims to try, nor in the recovery instructions.
    assert "brew install pyyaml" not in out, (
        f"installer output references the dead `brew install pyyaml` "
        f"recovery path:\n{out}"
    )
    assert "Homebrew Python detected" not in out, (
        f"installer announces a Homebrew strategy that no longer exists:\n{out}"
    )

    # Recovery message must give actionable manual alternatives.
    assert "--break-system-packages" in out
    assert "venv" in out
