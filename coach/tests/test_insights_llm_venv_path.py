"""coach/bin/insights-llm.sh — plugin-context PATH wedge.

When `CLAUDE_PLUGIN_DATA` is set + a `venv/bin/python3` exists under
it, insights-llm.sh prepends that dir to `PATH` at startup, so all
subsequent `python3` invocations (including child processes calling
aggregate_facets.py / merge.py) resolve to the plugin's venv
interpreter — and therefore find PyYAML on a plugin-only fresh box.

CLI users never have `CLAUDE_PLUGIN_DATA` set; for them the wedge is
a no-op. Same script ships into both distributions via
`tools/build_plugin.py`.

These tests verify the wedge fires (or doesn't) under the right
env conditions. They don't run the full insights pipeline — that's
covered by other test modules. Just the PATH semantics.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSIGHTS_LLM = REPO_ROOT / "coach" / "bin" / "insights-llm.sh"


def _run_path_probe(env: dict[str, str]) -> str:
    """Inject a tiny probe at the top of insights-llm.sh's already-run
    PATH-wedge logic. We can't easily run the full script (it'd start
    a real claude -p), so we extract the wedge block + run it
    standalone and dump the resulting PATH.

    The wedge is the first ~10 lines after `set -uo pipefail`. We
    simulate it inline so behavior tests don't depend on the rest of
    the script.
    """
    wedge = r"""
set -uo pipefail
if [[ -n "${CLAUDE_PLUGIN_DATA:-}" && -x "$CLAUDE_PLUGIN_DATA/venv/bin/python3" ]]; then
  export PATH="$CLAUDE_PLUGIN_DATA/venv/bin:$PATH"
fi
echo "$PATH"
"""
    result = subprocess.run(
        ["bash", "-c", wedge],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_wedge_fires_when_plugin_data_and_venv_present(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))

    path = _run_path_probe({})
    assert path.startswith(str(venv_bin) + ":"), (
        f"venv bin should be prepended to PATH; got: {path!r}"
    )


def test_wedge_skips_when_plugin_data_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    path = _run_path_probe({})
    # Just assert no /venv/bin marker injected; PATH content depends
    # on the test environment.
    assert "/venv/bin:" not in path or "/.coach-venv/" in path, (
        f"PATH should be unchanged when CLAUDE_PLUGIN_DATA unset; got: {path!r}"
    )


def test_wedge_skips_when_venv_python_missing(tmp_path, monkeypatch):
    """CLAUDE_PLUGIN_DATA set but no venv/bin/python3 exists yet (e.g.,
    the very first run before bootstrap.sh has set up the venv).
    Wedge should be a no-op — the test asserts the PATH doesn't pick
    up the (nonexistent) venv path."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    # No venv directory created.
    path = _run_path_probe({})
    expected_marker = str(tmp_path / "venv" / "bin")
    assert expected_marker not in path, (
        f"PATH should NOT include nonexistent venv bin; got: {path!r}"
    )


def test_real_insights_llm_sh_contains_the_wedge():
    """Sanity: pin that the wedge block is actually present in the
    real script (so a future refactor doesn't accidentally remove
    it)."""
    body = INSIGHTS_LLM.read_text()
    assert "CLAUDE_PLUGIN_DATA" in body, (
        "insights-llm.sh has lost its CLAUDE_PLUGIN_DATA wedge — the "
        "plugin's venv won't be picked up by child python invocations"
    )
    assert "venv/bin/python3" in body, (
        "insights-llm.sh wedge no longer references venv/bin/python3 "
        "as the existence-check probe"
    )
