---
description: "Diagnose Coach Claw plugin state — install, statusLine, cron, venv, defer marker. Pre-uninstall cleanup via --uninstall-prep [--wipe-data]. Also --remove-statusline, --prune-cache. Usage: /coach-claw:doctor [--uninstall-prep [--wipe-data]] [--remove-statusline] [--prune-cache [--dry-run]] [--json]"
---

The plugin and the npm CLI distribution coexist on the same box; both
read and write `~/.claude/coach/`. When something looks off (statusLine
missing or wrong, plugin appears unresponsive, slash commands fail to
load), this skill is the first stop. It's read-only by default and
prints a five-probe report.

Two mutating modes:

- **`--uninstall-prep`** (recommended pre-uninstall flow) — clears the
  plugin's `statusLine` entry AND writes the
  `~/.claude/coach/.uninstall-prepped` bypass marker so the canonical
  `/plugin uninstall coach-claw@coach-claw-plugins` runs without
  hitting the v0.1.20+ intercept. Optional `--wipe-data` also archives
  `~/.claude/coach/` to `~/.claude/coach.bak.<TS>/` via `mv` (never
  `rm`), so XP, profile, banked sessions, and `.user_config.json` are
  preserved as a restorable backup.
- **`--remove-statusline`** (lower-level) — clears just the
  `statusLine` entry without writing the bypass marker. Use this if
  you only want to peel off the statusline integration but keep the
  plugin installed and active. Note that `/plugin uninstall
  coach-claw@coach-claw-plugins` will still hit the intercept until
  you also run `--uninstall-prep`.

## Argument

The argument after `/coach-claw:doctor` is one of:

- (none) — print the full diagnostic report
- `--uninstall-prep` — pre-uninstall cleanup: clears the Coach
  `statusLine` from `~/.claude/settings.json` and writes the
  `.uninstall-prepped` marker. The next `/plugin uninstall
  coach-claw@coach-claw-plugins` is then authorized to proceed
  without warning. Preserves XP, profile, banked sessions, and
  `.user_config.json` by default. If `statusLine` cleanup fails, the
  marker is NOT written (so the intercept stays armed for the user's
  next attempt).
- `--wipe-data` — pairs with `--uninstall-prep` only. ALSO archives
  `~/.claude/coach/` to `~/.claude/coach.bak.<TS>/` via `mv` (never
  `rm`), recording the archive path in the marker so the user can
  restore by moving the dir back.
- `--remove-statusline` — clear only the `statusLine` entry from
  `~/.claude/settings.json` (no-op when it currently points at a
  non-Coach command). Does NOT write the `.uninstall-prepped` bypass
  marker — `/plugin uninstall coach-claw@coach-claw-plugins` will
  still trigger the v0.1.20+ intercept. Prefer `--uninstall-prep` for
  a clean uninstall path.
- `--prune-cache` — remove `~/.claude/plugins/cache/coach-claw-plugins/
  coach-claw/<version>/` dirs older than the installed versions.
  Claude Code's `/plugin update` never garbage-collects prior
  versions; over time these accumulate disk space. Every version
  listed in `installed_plugins.json` plus the live
  `$CLAUDE_PLUGIN_ROOT` are protected, with an N-3 predecessor buffer
  below each. Combines with `--dry-run` to preview.
- `--dry-run` — pairs with `--prune-cache` only. Lists what would be
  removed without deleting.
- `--json` — emit the same probe results as machine-readable JSON.
  Useful for bug-report triage. Cannot combine with
  `--uninstall-prep`, `--remove-statusline`, or `--prune-cache`.

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

- **Read-only by default.** Without an explicit mutating flag
  (`--uninstall-prep`, `--remove-statusline`, or `--prune-cache`),
  this skill must not mutate anything — including marker files.
  Plugin state is inspected in place.
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
