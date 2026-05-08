---
description: "Toggle the autonomous Coach Claw on/off, inspect state, or get uninstall guidance. Usage: /coach-claw:coach <on|off|status|uninstall>"
---

The Coach is an autonomous system: a daily insights pass updates
`~/.claude/coach/profile.yaml`; a `SessionStart` hook injects the active
profile as context so Claude can append at most one observational footnote
per session when your behavior matches a tracked pattern.

This skill is the **control surface** for that system. It does NOT review
proposed changes, approve deltas, or edit entries by hand. It only toggles
and inspects. Profile updates happen autonomously through
`/coach-claw:coach-insights`.

## Argument

The argument after `/coach-claw:coach` is one of:
`on` | `off` | `status` | `uninstall`. If no argument is provided, default
to `status`.

## Behavior per argument

### `off`
1. Create `~/.claude/coach/.disabled` as an empty flag file.
2. Confirm with one line: "Coach disabled. Hook will exit silently on SessionStart."

### `on`
1. Remove `~/.claude/coach/.disabled` if present.
2. Confirm with one line: "Coach enabled."

### `status` (default)
Invoke `${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/status.py` via Bash and print its output verbatim.
The script emits an ANSI-colored breakdown:

- Current level, XP total, wide progress bar, distance to next level
- Lifetime XP breakdown (graduations, longest active clean streak, banked
  session XP with 10:1 discount)
- Session XP breakdown (test runs, commits, unique skills invoked in the
  current session — live from the most recently modified transcript)
- "How to earn more" cheat sheet
- Profile state (active / probationary / retired counts + probationary
  streak values)
- Last insights run summary

Then, as a separate short line below the report, also surface:
- Whether `.disabled` is present (`Coach disabled` / `Coach enabled`)
- Whether `COACH_DISABLE=1` env is set (note the override)

Use only `${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/status.py`
and bash existence checks for `.disabled` / env var. Do not mutate
anything. (`run.sh` routes through the plugin's PyYAML venv so the
status script imports `yaml` cleanly even on a fresh box that has no
system PyYAML.)

### `uninstall`

Plugin uninstall is handled by Claude Code's `/plugin uninstall` command,
not by this skill — it manages the plugin's installed footprint cleanly.

Tell the user:

> "To uninstall the Coach Claw plugin, run `/plugin uninstall coach-claw`.
> That removes the plugin's hooks, skills, and code from
> `~/.claude/plugins/cache/`. Your coaching state in `~/.claude/coach/`
> (profile.yaml, banked XP, git history) is preserved — uninstalling the
> plugin doesn't touch your home directory state."
>
> "The plugin may have patched `~/.claude/settings.json:statusLine`
> during install. To clean that up, run
> `/coach-claw:doctor --remove-statusline` BEFORE you uninstall, or
> manually remove the `statusLine` key from `~/.claude/settings.json`
> after."
>
> "If you also installed the npm CLI version
> (`@rm0nroe/coach-claw`), uninstalling the plugin doesn't affect it.
> Run `npx @rm0nroe/coach-claw uninstall` separately if you want to
> remove that too."

Do not attempt to mutate `settings.json`, the plugin cache directory,
or any launchd plist from this skill. The plugin lifecycle owns those.

## Rules

- Never edit `profile.yaml` entries by hand from this skill. The autonomous
  loop is the point; hand-edits create drift.
- Never `rm -rf` anything. State preservation is non-negotiable.
- Only the `.disabled` flag file is mutated by this skill (created on `off`,
  removed on `on`). Everything else in `~/.claude/coach/` is read-only from
  here.
- If the user asks to change nudge wording, cooldown, decay rate, cap,
  or thresholds, point them at the constants at the top of
  `${CLAUDE_PLUGIN_ROOT}/hooks/coach-session-start.py` (hook-side) or
  `${CLAUDE_PLUGIN_ROOT}/skills/coach-insights/SKILL.md` (runner-side).
  Don't invent a config file — the constants are the config.
