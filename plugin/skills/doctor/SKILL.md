---
description: "Diagnose Coach Claw plugin state — install, statusLine, cron, venv, defer marker. Also cleans up the statusLine key on uninstall. Usage: /coach-claw:doctor [--remove-statusline] [--json]"
---

The plugin and the npm CLI distribution coexist on the same box; both
read and write `~/.claude/coach/`. When something looks off (statusLine
missing or wrong, plugin appears unresponsive, slash commands fail to
load), this skill is the first stop. It's read-only by default and
prints a five-probe report. The only mutating mode is
`--remove-statusline`, which is the documented uninstall-cleanup path
since Claude Code's plugin lifecycle does NOT clear `statusLine` from
`settings.json` when a plugin is uninstalled.

## Argument

The argument after `/coach-claw:doctor` is one of:

- (none) — print the full diagnostic report
- `--remove-statusline` — clear the plugin's `statusLine` entry from
  `~/.claude/settings.json` (only if it currently points at Coach;
  otherwise no-op). Use this BEFORE running `/plugin uninstall
  coach-claw` if you want a clean uninstall.
- `--json` — emit the same probe results as machine-readable JSON.
  Useful for bug-report triage. Cannot combine with
  `--remove-statusline`.

## Steps

```bash
${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/doctor.py "$@"
```

Capture the script's stdout and print it verbatim. The script handles
its own atomic writes (for `--remove-statusline`), error reporting,
and exit codes. Do NOT post-process or summarize the output —
the report shape is part of the contract for bug triage.

## What the report covers

1. **Plugin install** — which marketplace served the plugin, the
   installed version, and the cache path. Reads
   `~/.claude/plugins/installed_plugins.json`.
2. **Coexistence** — whether `~/.claude/coach/.plugin-deferred` is
   present (plugin is yielding to the CLI) or absent (plugin is the
   active hook surface).
3. **statusLine ownership** — classifies `~/.claude/settings.json:
   statusLine` as one of:
   - **ours (plugin)** — points at the plugin's `bootstrap.sh +
     default_statusline.py`
   - **ours (CLI)** — points at the CLI's
     `default-statusline-command.sh`
   - **claimed** — points elsewhere; plugin will not overwrite
   - **absent** — no statusLine key at all
4. **Cron schedule** — whether the daily insights cron is registered
   (launchd on macOS, crontab on Linux). Reuses `cron_check.py`.
5. **Venv health** — whether `${CLAUDE_PLUGIN_DATA}/venv/bin/python3`
   exists and can `import yaml`. The venv is the plugin's only path to
   PyYAML on a fresh box without the CLI installed.

## Rules

- **Read-only by default.** Without `--remove-statusline`, this skill
  must not mutate anything — including marker files. Plugin state is
  inspected in place.
- **Never edit `settings.json` by hand from this skill.** The Python
  script does it under flock + atomic write (mirrors merge.py and
  statusline_self_patch.py). Editing in shell breaks the safety
  contract.
- **`--remove-statusline` is targeted.** It only clears the
  `statusLine` key when the existing entry matches a Coach marker
  (`default_statusline.py` or `default-statusline-command.sh`). A
  user's custom statusLine is left alone; the script reports
  "claimed" instead.
- **Never delete `~/.claude/coach/`** or anything in it from this
  skill. Profile state is shared across distributions and preserved
  across uninstall by design.
- **Don't run `/plugin uninstall` from within this skill.** That's
  Claude Code's lifecycle; the report and the cleanup flag are this
  skill's whole job.
