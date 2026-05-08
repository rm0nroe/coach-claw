# Coach Claw — data directory

This directory is the live state of your coach: your tracked patterns,
lifetime XP, banked sessions, and one-line-per-run changelog. Everything
here is git-tracked (so profile mutations are rollback-able commits)
and stays local to your machine — Coach never independently uploads
`profile.yaml`, the changelog, the session ledger, or any redacted
example. (The weekly LLM path invokes `claude -p "/insights"` for the
side effect of refreshing local sidecars in `~/.claude/usage-data/`;
that goes through Claude Code, not Coach. See
`artifacts/infrastructure.md` § Security.)

## Files

| File | Purpose |
|---|---|
| `profile.yaml` | Active + probationary + candidate entries, graduated patterns, neutral archives, skill hints, split XP accounting (`session_banked_xp`, `milestone_xp`, `graduation_xp`, `manual_adjustments`). Source of truth. |
| `changelog.md` | One line per `/coach-insights` run — what got added, promoted, retired. |
| `banked_sessions.json` | Per-session XP ledger, keyed by session UUID. Populated by `bank.py` at SessionStart. |
| `log.ndjson` | Bounded redacted `tip_fired` / `tip_completed` events. No transcript text, tool commands, examples, or generated tip prose. |
| `.disabled` | Flag file — if present, both hooks exit silently. Removed by `/coach on`. |
| `.lock` | `flock` file used by `merge.py` + `bank.py` for profile writes. |
| `.pending_*` | Markers the hook consumes on next turn (graduation, streak tick, regression, level-up). |
| `.tip_state.json` | Scheduler state — last-fire timestamps, pending completions, global cooldown. |
| `.level_state.json` | High-water-mark level index; never moves downward (prevents false re-celebration). |

## Control

```bash
/coach on            # remove .disabled
/coach off           # write .disabled (hooks silent until re-enabled)
/coach status        # level, XP breakdown, per-pattern streaks, last /coach-insights run
/coach uninstall     # move this data dir to ~/.claude/coach.bak.<ts>/
COACH_DISABLE=1 claude   # one-shot bypass without writing state
```

## Backup hygiene

Re-running `npx @rm0nroe/coach-claw@latest install` or `./install.sh` snapshots
the existing coach dir to
`~/.claude/coach.bak.<ts>/` and leaves byte-different `settings.json` /
`hooks/*.py` snapshots beside their live counterparts. Byte-identical
re-installs do not leave behind redundant `.bak.<ts>` siblings, and the
installer prunes by default — keeping the 3 most recent of each kind
(`coach.bak.*`, `settings.json.bak.*`, `hooks/<hook>.bak.*`) so backups
don't accumulate over months of upgrades.

To opt out (e.g. holding a specific `.bak` for forensic recovery):

```bash
./install.sh --no-prune-backups
```

Manual equivalent of the prune for a one-shot cleanup outside the
installer:

```bash
ls -dt ~/.claude/coach.bak.*       | tail -n +4 | xargs rm -rf
ls -t  ~/.claude/settings.json.bak.* | tail -n +4 | xargs rm -f
ls -t  ~/.claude/hooks/*.bak.*       | tail -n +4 | xargs rm -f
```

## Reinstall & Uninstall Safety

Re-running `npx @rm0nroe/coach-claw@latest install` or `./install.sh` from the
bundle preserves coach state, including XP, tracked patterns, the session
ledger, cooldowns, pending markers, and the disabled flag. `/coach uninstall`
is reversible: it renames this directory to
`~/.claude/coach.bak.<timestamp>/` instead of deleting it, then removes the
coach hook entries from `settings.json`.

For a clean setup test, uninstall, run `npx @rm0nroe/coach-claw@latest install --seed`
or `./install.sh --seed`, verify the new install, then copy the saved progress
files from the `coach.bak.<timestamp>/` directory back into
`~/.claude/coach/`. For an exact restore, also copy hidden runtime state files
if present: `.level_state.json`, `.tip_state.json`, `.last_session_start`, and
`log.ndjson`.

## Rollback

Every `/coach-insights` run is a commit in `~/.claude/coach/`:

```bash
git -C ~/.claude/coach log --oneline | head
git -C ~/.claude/coach diff HEAD~1 -- profile.yaml     # see what changed
git -C ~/.claude/coach checkout HEAD~1 -- profile.yaml # roll back one run
```

Hand-edits are welcome — the merge logic preserves explicit fields. If
a tracked weakness is wrong, edit the nudge or delete the entry directly.

## Tests

The parent install also copied `tests/` here. After you have `pytest`
installed (`python3 -m pip install --user pytest`):

```bash
cd ~/.claude/coach && python3 -m pytest tests/
```
