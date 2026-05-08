"""render_env.py — terminal vs IDE detection from CLAUDE_CODE_ENTRYPOINT."""
from __future__ import annotations

from render_env import detect_render_env


# -----------------------------------------------------------------------------
# Defaults / unset
# -----------------------------------------------------------------------------

def test_empty_env_is_terminal():
    assert detect_render_env({}) == "terminal"


def test_unset_entrypoint_is_terminal():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": ""}) == "terminal"


# -----------------------------------------------------------------------------
# Known terminal entrypoints
# -----------------------------------------------------------------------------

def test_cli_is_terminal():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "cli"}) == "terminal"


def test_mcp_is_terminal():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "mcp"}) == "terminal"


def test_sdk_py_is_terminal():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "sdk-py"}) == "terminal"


def test_sdk_ts_is_terminal():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"}) == "terminal"


# -----------------------------------------------------------------------------
# Known IDE entrypoints
# -----------------------------------------------------------------------------

def test_vscode_is_ide():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "vscode"}) == "ide"


def test_claude_vscode_is_ide():
    """Cursor's Claude Code integration reports this value (verified 2026-05)."""
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "claude-vscode"}) == "ide"


def test_jetbrains_is_ide():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "jetbrains"}) == "ide"


def test_claude_jetbrains_is_ide():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "claude-jetbrains"}) == "ide"


def test_ide_onboarding_is_ide():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "ide-onboarding"}) == "ide"


# -----------------------------------------------------------------------------
# Normalization
# -----------------------------------------------------------------------------

def test_entrypoint_is_case_insensitive():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "VSCODE"}) == "ide"
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "Claude-VSCode"}) == "ide"


def test_entrypoint_whitespace_is_stripped():
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "  vscode  "}) == "ide"


# -----------------------------------------------------------------------------
# Allowlist semantics — unknown values default to terminal
# -----------------------------------------------------------------------------

def test_unknown_entrypoint_defaults_to_terminal():
    """Future / unrecognized entrypoints fall through to the safe default.
    Terminal shape uses universal markdown and renders acceptably everywhere."""
    assert detect_render_env({"CLAUDE_CODE_ENTRYPOINT": "some-future-surface"}) == "terminal"


# -----------------------------------------------------------------------------
# COACH_RENDER_ENV override
# -----------------------------------------------------------------------------

def test_override_forces_ide_over_terminal_entrypoint():
    assert detect_render_env({
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "COACH_RENDER_ENV": "ide",
    }) == "ide"


def test_override_forces_terminal_over_ide_entrypoint():
    assert detect_render_env({
        "CLAUDE_CODE_ENTRYPOINT": "vscode",
        "COACH_RENDER_ENV": "terminal",
    }) == "terminal"


def test_override_is_case_insensitive():
    assert detect_render_env({
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "COACH_RENDER_ENV": "IDE",
    }) == "ide"


def test_invalid_override_falls_back_to_entrypoint_detection():
    """Garbage override is ignored; entrypoint detection still runs."""
    assert detect_render_env({
        "CLAUDE_CODE_ENTRYPOINT": "vscode",
        "COACH_RENDER_ENV": "bogus",
    }) == "ide"
    assert detect_render_env({
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "COACH_RENDER_ENV": "bogus",
    }) == "terminal"


# -----------------------------------------------------------------------------
# Default: uses os.environ when no env arg provided
# -----------------------------------------------------------------------------

def test_uses_os_environ_when_env_arg_omitted(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "vscode")
    monkeypatch.delenv("COACH_RENDER_ENV", raising=False)
    assert detect_render_env() == "ide"


def test_uses_os_environ_default_terminal(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("COACH_RENDER_ENV", raising=False)
    assert detect_render_env() == "terminal"
