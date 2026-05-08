#!/usr/bin/env bash
# Coach Claw plugin — hook-invocation wrapper.
#
# Two responsibilities:
#   1. Coexistence guard. If the npm CLI distribution has Coach hooks
#      already registered in ~/.claude/settings.json, exit 0 silently
#      so the plugin's hook does nothing — the CLI wins. Without this,
#      both distributions' hooks would fire on every event (double XP,
#      double tip rendering, etc.).
#   2. Delegate to run.sh for the actual venv-or-system Python exec.
#
# Skills should NOT use this script (they're explicit user invocations
# and shouldn't defer). They use run.sh directly. This split is what
# made the slash-commands-bypass-bootstrap design point safe to
# resolve cleanly in v0.1.2.
#
# Invocation (from hooks/hooks.json):
#   ${CLAUDE_PLUGIN_ROOT}/bin/bootstrap.sh ${CLAUDE_PLUGIN_ROOT}/hooks/coach-session-start.py

# Coexistence guard: defer to the npm CLI distribution if its hooks are
# already registered in ~/.claude/settings.json. Cheap (single read).
# Exit code 10 from coexistence_check.py means "CLI wins; plugin
# self-disables for this hook fire."
if [ -x "$(command -v python3)" ] && [ -f "${CLAUDE_PLUGIN_ROOT}/bin/coexistence_check.py" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/coexistence_check.py"
  if [ $? -eq 10 ]; then
    exit 0
  fi
fi

# Hand off to run.sh for venv setup + python exec.
exec "${CLAUDE_PLUGIN_ROOT}/bin/run.sh" "$@"
