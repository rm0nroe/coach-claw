---
description: "Toggle the autonomous Coach Claw on/off, inspect state, or uninstall. Usage: /coach <on|off|status|uninstall>"
---

The Coach is an autonomous system: `/coach-insights` runs daily and maintains
`~/.claude/coach/profile.yaml`; a `SessionStart` hook injects the active
profile as context so Claude can append at most one observational footnote
per session when your behavior matches a tracked pattern.

This skill is the **control surface** for that system. It does NOT review
proposed changes, approve deltas, or edit entries by hand. It only toggles
and inspects. Profile updates happen autonomously through `/coach-insights`.

## Argument

The argument after `/coach` is one of: `on` | `off` | `status` | `uninstall`.
If no argument is provided, default to `status`.

## Behavior per argument

### `off`
1. Create `~/.claude/coach/.disabled` as an empty flag file.
2. Confirm with one line: "Coach disabled. Hook will exit silently on SessionStart."

### `on`
1. Remove `~/.claude/coach/.disabled` if present.
2. Confirm with one line: "Coach enabled."

### `status` (default)
Invoke `~/.claude/coach/bin/status.py` via Bash and print its output verbatim.
The script emits an ANSI-colored breakdown:

- Current level, XP total, wide progress bar, distance to next level
- Lifetime XP breakdown (graduations, longest active clean streak, banked
  session XP with 10:1 discount)
- Session XP breakdown (test runs, commits, unique skills invoked in the
  current session — live from the most recently modified transcript)
- "How to earn more" cheat sheet
- Profile state (active / probationary / retired counts + probationary
  streak values)
- Last `/coach-insights` run summary

Then, as a separate short line below the report, also surface:
- Whether `.disabled` is present (`Coach disabled` / `Coach enabled`)
- Whether `COACH_DISABLE=1` env is set (note the override)

Use only `python3 ~/.claude/coach/bin/status.py` and bash existence
checks for `.disabled` / env var. Do not mutate anything.

### `uninstall`
This is a reversible but disruptive action. Compute `TS=$(date +%Y%m%d-%H%M%S)`
once and reuse it for every `.bak.<ts>` / `.uninstalled.<ts>` suffix below so
the whole uninstall is one timestamped batch. Before doing anything:
1. Ask the user for explicit confirmation ("This will move `~/.claude/coach/`
   to `~/.claude/coach.bak.<TS>/`, move both coach hook scripts to
   `.uninstalled.<TS>` (reversible rename), remove the coach
   `SessionStart` + `UserPromptSubmit` hook entries from `settings.json`,
   and unload + rename the daily-insights launchd plist (macOS) or print
   the cron line for you to remove (Linux). Proceed?").
2. Only on explicit "yes":
   a. `mv ~/.claude/coach ~/.claude/coach.bak.<TS>` — **never `rm`**.
      Investigate before deleting any application-data directory; prefer
      a reversible rename to preserve the data.
   b. Move the hook scripts so the live `~/.claude/hooks/` no longer
      carries dead coach hooks (settings.json no longer references them
      after step d, but stale files are confusing in any future debug):
      - `mv ~/.claude/hooks/coach-session-start.py ~/.claude/hooks/coach-session-start.py.uninstalled.<TS>`
      - `mv ~/.claude/hooks/coach-user-prompt.py ~/.claude/hooks/coach-user-prompt.py.uninstalled.<TS>`
      Skip silently if a file isn't present.
   c. Back up settings: `cp ~/.claude/settings.json ~/.claude/settings.json.bak.<TS>`.
   d. Edit `~/.claude/settings.json` to remove only coach hook entries:
      `hooks.SessionStart` commands containing `coach-session-start.py` and
      `hooks.UserPromptSubmit` commands containing `coach-user-prompt.py`.
      Preserve all other hooks and the rest of the file verbatim. Validate
      the result is still valid JSON (`python3 -c "import json; json.load(open('...'))"`).
   e. Unregister the daily Coach insights scheduler.
      - macOS: if `~/Library/LaunchAgents/com.local.claude-coach.plist` exists,
        `launchctl unload ~/Library/LaunchAgents/com.local.claude-coach.plist 2>/dev/null`
        then `mv ~/Library/LaunchAgents/com.local.claude-coach.plist
        ~/Library/LaunchAgents/com.local.claude-coach.plist.uninstalled.<TS>`.
        The unload is best-effort (already-unloaded plists return non-zero
        but cause no harm); the `mv` is the load-bearing step that prevents
        the next `launchctl bootstrap` from re-loading it.
      - Linux: `crontab` cannot be safely edited from a skill (the user's
        crontab may have many lines we shouldn't touch). Print:
        "Run `crontab -e` and remove the line containing
        `coach/bin/insights.sh`."
   f. Confirm with: "Coach uninstalled. Backups at:
      `~/.claude/coach.bak.<TS>/`,
      `~/.claude/hooks/coach-*.py.uninstalled.<TS>`,
      `~/.claude/settings.json.bak.<TS>`,
      `~/Library/LaunchAgents/com.local.claude-coach.plist.uninstalled.<TS>` (macOS).
      To restore: `mv` each backup back to its original path and re-run
      `launchctl load ...plist` (macOS) or re-add the cron line (Linux)."

## Rules

- Never edit `profile.yaml` entries by hand from this skill. The autonomous
  loop is the point; hand-edits create drift.
- Never `rm -rf` anything. Use `mv` to a `.bak` path.
- Never touch the `enabledPlugins`, `permissions`, or `env` blocks of
  `settings.json`. Only the `hooks` block.
- If the user asks to change nudge wording, cooldown, decay rate, cap,
  or thresholds, point them at the constants at the top of
  `~/.claude/hooks/coach-session-start.py` (hook-side) or
  `~/.claude/skills/coach-insights/SKILL.md` (runner-side). Don't invent a config
  file — the constants are the config.
