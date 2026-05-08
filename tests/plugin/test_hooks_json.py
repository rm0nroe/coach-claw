"""plugin/hooks/hooks.json — schema shape required by Claude Code.

Plugin hooks files wrap the event map under a top-level `hooks` key.
The settings.json shape (which has `hooks` directly at top level for
the consumer) is DIFFERENT from this. Easy to confuse — the official
validator (`claude plugin validate`) catches it but only if you run
it; this test bakes the requirement into CI.

Regression guard: 2026-05-08 the manifest shipped initially with the
flat shape (`{SessionStart: [...], UserPromptSubmit: [...]}`) at the
top level and `claude plugin validate` flagged it as `hooks: Invalid
input: expected record, received undefined`.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_JSON = REPO_ROOT / "plugin" / "hooks" / "hooks.json"


def _data():
    return json.loads(HOOKS_JSON.read_text())


def test_hooks_json_is_valid_json():
    _data()


def test_top_level_hooks_key_present():
    data = _data()
    assert "hooks" in data, (
        "plugin/hooks/hooks.json must wrap the event map under a "
        "top-level `hooks` key. The flat shape (`{SessionStart: [...], "
        "UserPromptSubmit: [...]}` at root) is the SETTINGS.JSON shape "
        "and is rejected by the plugin manifest validator."
    )
    assert isinstance(data["hooks"], dict), "hooks must be a JSON object"


def test_hooks_contain_required_events():
    """Coach plugin needs SessionStart (statusLine self-patch + bank)
    AND UserPromptSubmit (tip rendering, celebrate banners, cron
    nudge). Neither is optional — pin both."""
    hooks = _data()["hooks"]
    for event in ("SessionStart", "UserPromptSubmit"):
        assert event in hooks, f"hooks.json missing required event: {event}"
        assert isinstance(hooks[event], list) and hooks[event], (
            f"hooks.{event} must be a non-empty array of hook groups"
        )


def test_hook_commands_use_plugin_root():
    """All hook commands must reference ${CLAUDE_PLUGIN_ROOT} so they
    resolve correctly inside the plugin cache directory. Hardcoded
    paths like `~/.claude/...` in hook commands would point at the
    npm CLI install (or break on plugin reinstall)."""
    hooks = _data()["hooks"]
    for event_name, groups in hooks.items():
        for grp in groups:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                assert "${CLAUDE_PLUGIN_ROOT}" in cmd, (
                    f"{event_name} hook command does not reference "
                    f"${{CLAUDE_PLUGIN_ROOT}}: {cmd!r}"
                )


def test_hook_commands_invoke_bootstrap():
    """The plugin's hook commands should go through bootstrap.sh, which
    sets up the per-plugin PyYAML venv. Direct python3 invocation
    would crash inside the hook on systems without PyYAML installed
    globally."""
    hooks = _data()["hooks"]
    for event_name, groups in hooks.items():
        for grp in groups:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                assert "bootstrap.sh" in cmd, (
                    f"{event_name} hook should route through "
                    f"${{CLAUDE_PLUGIN_ROOT}}/bin/bootstrap.sh so the "
                    f"PyYAML venv gets provisioned. Got: {cmd!r}"
                )
