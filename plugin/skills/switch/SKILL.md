---
description: "Flip Coach control from the npm CLI to this plugin. Removes CLI hook entries from settings.json so the plugin's hooks fire instead. Usage: /coach-claw:switch [--dry-run]"
---

When a user has both the npm CLI distribution (`@rm0nroe/coach-claw`)
and this plugin installed, the plugin's coexistence guard defers to the
CLI by default — the CLI was likely installed first and manages OS-side
bits (launchd cron) the plugin can't reach. This skill is the
**explicit handoff** when the user wants the plugin to take over.

## What this does

1. Reads `~/.claude/settings.json`.
2. Removes any hook entries (SessionStart / UserPromptSubmit) whose
   command references `coach-session-start.py` or `coach-user-prompt.py`
   AND does NOT live under `${CLAUDE_PLUGIN_ROOT}` (i.e., CLI hooks).
3. Removes the CLI's `statusLine` entry if present (uses
   `default-statusline-command.sh`). The plugin's SessionStart hook
   will reinstall its own statusLine on the next session.
4. Writes `~/.claude/coach/.cli-uninstalled-by-plugin` marker.
5. Clears any stale `~/.claude/coach/.plugin-deferred` marker.

The npm CLI's installed Python files (`~/.claude/hooks/coach-*.py`,
`~/.claude/coach/bin/`) are NOT touched by this skill. They remain on
disk but unreferenced from settings.json. The user can run
`npx @rm0nroe/coach-claw uninstall` separately for full CLI cleanup.

The user's coaching state in `~/.claude/coach/` (profile.yaml, banked
XP, git history) is shared between distributions and is preserved
across the switch.

## Argument

- `--dry-run` (optional): print what would change without writing.

## Steps

```bash
${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/switch_to_plugin.py "$@"
```

Capture the script's stdout and print it verbatim. The script handles
its own atomic writes, error reporting, and exit codes.

If the user has not yet started a new Claude Code session, remind them:
"Restart Claude Code (or open a new session) for the plugin's hooks
and statusLine to take effect."

## Rules

- **Never edit `settings.json` by hand from this skill.** The Python
  script does it under flock + atomic write. Editing in shell breaks
  the safety contract and can corrupt settings.json on concurrent
  writes.
- **Never delete `~/.claude/coach/`** or anything in it from this
  skill. State preservation is non-negotiable; the script only writes
  marker files inside it.
- **Don't try to also `npx @rm0nroe/coach-claw uninstall`** from
  within this skill. That's a separate user decision — the script's
  output reminds them about it.
