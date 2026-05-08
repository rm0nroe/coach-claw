# Infrastructure — Coach Claw

Deployment + operations guide. For architecture see
[`architecture.md`](./architecture.md). For troubleshooting see
[`../README.md`](../README.md#troubleshooting).

## Current state

Coach is a **local-only** tool that ships as an npm installer wrapper
or a cloneable repo and installs into the user's `~/.claude/` directory
via the same `install.sh`.
There is no remote backend, container, or cloud service.

## Requirements

| | Required | Recommended | Notes |
|---|---|---|---|
| OS | macOS 11+ or Linux | macOS 13+, Ubuntu 22.04+ | Windows via WSL is untested |
| Python | 3.8+ | 3.11+ | `from __future__ import annotations` + f-strings + `fcntl` |
| PyYAML | ≥5.4 | 6.x | `install.sh` tries pip --user, then pip --user --break-system-packages |
| pytest | optional | 7+ | For running the test suite |
| launchd | macOS only | — | Installed by default |
| cron | Linux only | — | Installed by default |
| `git` | yes | 2.x | `install.sh` uses `git init` for rollback |
| Node/npm | npm install path only | Node 18+ | `npx @rm0nroe/coach-claw@latest ...` wrapper; no runtime JS service |
| Claude Code CLI | ≥ Jan 2026 build | latest | Hook protocol support required |

Hardware: anything. Profile + tests total <20 MB on disk. Hook execution
is <50ms typical, sub-3s always (Claude Code hook timeout ceiling).

## Service inventory

| Component | Type | Trigger | Writes | Read by |
|---|---|---|---|---|
| `coach-session-start.py` | Hook (sync) | Claude Code `SessionStart` | `.last_session_start` | Claude Code (as `additionalContext`) |
| `coach-user-prompt.py` | Hook (sync) | Claude Code `UserPromptSubmit` | `.tip_state.json`, consumes `.pending_*` markers | Claude Code (as `additionalContext`) |
| `bank.py` | Detached subprocess | Spawned by `SessionStart` | `profile.yaml`, `banked_sessions.json` | `/coach status`, `stats.py` |
| `insights.sh` | Scheduled | launchd (macOS) / cron (Linux) | `profile.yaml`, `changelog.md`, `.pending_*` markers | SessionStart hook (loads profile) |
| `insights-llm.sh` | Detached subprocess + on-demand | SessionStart hook (when `.last_weekly_insights` mtime > 7d) OR `/coach-insights` skill (`--force`) | `profile.yaml`, `changelog.md`, `.pending_*` markers (via `merge.py`), `.last_weekly_insights` (throttle) | shells out to `claude -p "/insights"` for the side effect of refreshing facets sidecars; aggregates them via `aggregate_facets.py` |
| `aggregate_facets.py` | Helper | invoked by `insights-llm.sh` | stdout JSON detections | piped to `merge.py` |
| `/coach-insights` skill | On-demand | user types `/coach-insights` in Claude Code | (delegates to `insights-llm.sh --force`) | thin wrapper around `insights-llm.sh` |
| `default-statusline-command.sh` | On-demand | Claude Code statusline | — | trampolines into `default_statusline.py` |
| `default_statusline.py` | On-demand | invoked by wrapper | `.pending_levelup` (via `stats.render_segment`) | Claude Code (stdout rendering) |
| `stats.py` | Library | imported by `default_statusline.py` and `bank.py` | `.level_state.json`, `.pending_levelup` | direct CLI also supported |
| `status.py` | On-demand | `/coach status` slash command | — | user (stdout) |
| `user_config.py` | Library | imported by `stats.py` + `/config` skill | `.user_config.json` | display-config readers |
| `skill_inventory.py` | Helper | invoked by `insights.sh` | stdout JSON | piped to `merge.py` |

There are **no long-running processes**. Everything is invoke-on-demand
or scheduled.

## Deployment

### Fresh install (macOS or Linux)

Npm path:

```bash
npx @rm0nroe/coach-claw@latest doctor
npx @rm0nroe/coach-claw@latest install --seed
npx @rm0nroe/coach-claw@latest launchd                 # macOS only — daily cron
```

Checkout fallback:

```bash
cd coach-claw                       # the bundle
./install.sh                               # installs to ~/.claude/
./install-launchd.sh                       # macOS only — daily cron
```

Linux equivalent of `install-launchd.sh`:

```bash
crontab -e
# then add (runs daily at 04:00 local):
0 4 * * * $HOME/.claude/coach/bin/insights.sh 1d >> /tmp/claude-coach.log 2>&1
```

### Upgrade (re-install over existing)

Re-run `npx @rm0nroe/coach-claw@latest install` or `./install.sh` from the
updated bundle. The installer:

- Moves `~/.claude/coach/` → `~/.claude/coach.bak.<ts>/` (preserving a
  rollback point)
- **Restores** `profile.yaml`, `banked_sessions.json`, `changelog.md`,
  `.user_config.json`, scheduler state files (`.tip_state.json`,
  `.level_state.json`, `.last_session_start`, `.last_weekly_insights`),
  the `.disabled` flag, and any in-flight `.pending_*` markers from
  the backup. User XP + tracked patterns + `/config` choices + the
  weekly-insights throttle all survive upgrades. Full preserve list
  at `install.sh:218-230`.
- Backs up `settings.json` → `settings.json.bak.<ts>`
- Only adds hook entries if not already present (idempotent); the
  `statusLine` entry is only registered if no statusline already exists,
  so existing custom statuslines are left untouched
- Smoke-tests both hooks

### Rollback

```bash
# Option A: roll back the profile one /coach-insights run (keep binaries):
git -C ~/.claude/coach log --oneline | head
git -C ~/.claude/coach checkout HEAD~1 -- profile.yaml

# Option B: roll back the whole install to the previous backup:
rm -rf ~/.claude/coach
mv ~/.claude/coach.bak.<ts> ~/.claude/coach
cp ~/.claude/settings.json.bak.<ts> ~/.claude/settings.json
```

### Uninstall

```bash
# Inside Claude Code (recommended — does all five steps in one batch):
/coach uninstall

# Or by hand. Compute one TS up front so all backups share a suffix:
TS=$(date +%Y%m%d-%H%M%S)
mv ~/.claude/coach                          ~/.claude/coach.bak.$TS
mv ~/.claude/hooks/coach-session-start.py   ~/.claude/hooks/coach-session-start.py.uninstalled.$TS
mv ~/.claude/hooks/coach-user-prompt.py     ~/.claude/hooks/coach-user-prompt.py.uninstalled.$TS
cp ~/.claude/settings.json                  ~/.claude/settings.json.bak.$TS
# Then edit ~/.claude/settings.json to remove only the SessionStart +
# UserPromptSubmit hook entries whose `command` references coach-*.py.
# macOS launchd job — unload then RENAME (not rm) so reload is reversible:
launchctl unload ~/Library/LaunchAgents/com.local.claude-coach.plist 2>/dev/null
mv ~/Library/LaunchAgents/com.local.claude-coach.plist \
   ~/Library/LaunchAgents/com.local.claude-coach.plist.uninstalled.$TS
# Linux cron:
crontab -e     # remove the line containing coach/bin/insights.sh
```

To restore: `mv` each `.bak.$TS` / `.uninstalled.$TS` back to its
original path and (macOS) `launchctl load
~/Library/LaunchAgents/com.local.claude-coach.plist`.

## Build

No build step — pure Python + bash. `chmod +x` on the `.py` and `.sh`
files is done by `install.sh`. The pytest suite is optional and requires
`python3 -m pip install --user pytest` once.

## Configuration

### Environment variables

| Var | Effect |
|---|---|
| `COACH_DISABLE=1` | Both hooks silently exit. One-shot disable without writing `.disabled`. |
| `COACH_ALL_SKILLS=1` | `UserPromptSubmit` hook bypasses skill-relevance filtering — every installed skill becomes eligible regardless of project scope or topic overlap. Debugging / opt-out escape hatch. |
| `CLAUDE_DIR` | Overrides `~/.claude` target during `coach-claw install` / `./install.sh`. Used for sandboxed testing. |

### Tunables (hook-side)

Edit `~/.claude/hooks/coach-user-prompt.py`:

| Constant | Default | Effect |
|---|---|---|
| `TIP_FIRE_PROBABILITY` | 0.35 | Roll per UserPromptSubmit |
| `TIP_GLOBAL_COOLDOWN_SEC` | 300 | Min seconds between any two tips |
| `TIP_PER_TIP_COOLDOWN_HOURS` | 24 | Min hours before same tip fires again |
| `TIER_MULTIPLIER` | `{probationary: 1.5, active: 1.0, hint: 0.4}` | Weight bias for tier |
| `STREAK_URGENCY_{HIGH,MID,LOW}` | 1.3 / 1.0 / 0.6 | Weight bias by streak bucket |
| `MIN_SKILL_SHARE` | 0.25 | Floor for cumulative skill-hint weight |
| `SESSION_XP_CAP` | 15 | Raw XP cap per session |

### Tunables (merge-side)

Edit `~/.claude/coach/bin/merge.py`:

| Constant | Default | Effect |
|---|---|---|
| `CONFIDENCE_ON_NEW` | 0.20 | Starting confidence for a new candidate |
| `CONFIDENCE_BOOST` | 0.15 | Added on re-detection |
| `CONFIDENCE_DECAY_PER_DAY` | 0.05 | Subtracted per day untouched |
| `RETIRE_BELOW` | 0.30 | Confidence floor — falls here, weakness retires |
| `GC_CANDIDATE_AFTER_DAYS` | 14 | Candidate timeout (garbage-collected if never debounced) |
| `DEBOUNCE_THRESHOLD` | 2 | Detections needed in window to promote candidate |
| `DEBOUNCE_WINDOW` | 3 | Run-history window size |
| `PROBATIONARY_DAYS` | 7 | Probationary → active after this many days |
| `RETIRE_AFTER_ABSENT_RUNS` | 5 | Clean-streak runs to retire a negative |
| `POSITIVE_GRADUATION_RUNS` | 5 | Presence-streak runs to master a positive |
| `MAX_ACTIVE` | 10 | Hard cap on active tier (lowest c×p evicted) |
| `STREAK_XP_SCHEDULE` | `{1:1, 2:1, 3:1, 4:2}` | Mid-streak reward XP |

### Hook + statusline registration (in `~/.claude/settings.json`)

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command",
                  "command": "<PY> ~/.claude/hooks/coach-session-start.py",
                  "timeout": 3}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command",
                  "command": "<PY> ~/.claude/hooks/coach-user-prompt.py",
                  "timeout": 2}]}
    ]
  },
  "statusLine": {
    "command": "bash ~/.claude/coach/default-statusline-command.sh"
  }
}
```

`<PY>` is the absolute path to `python3` resolved by `install.sh` (via
`command -v python3`). The same path is `sed`-substituted into the
statusline trampoline at install time (`@PY@` → resolved interpreter).
Re-run `install.sh` if the interpreter moves. The `statusLine` entry is
only added on a fresh install — existing custom statuslines are left
untouched.

### `/config` (display tunables, persisted to `.user_config.json`)

Slash command surface for `~/.claude/coach/.user_config.json`. Inside
Claude Code:

```
/config show              # current settings
/config preview           # render every variant × theme
/config statusline <name> # crystal | pips | bracket | slash | forge
/config theme <name>      # craft | forge | cosmic | ocean | skyrim |
                          # marvel | dc | finalfantasy | military |
                          # lotr | starwars | hacker
/config elo <min> <max>   # default 1000 → 2800
/config reset             # restore all defaults
```

Settings persist across reinstalls (preserve list above). Authoritative
valid sets live at `coach/bin/user_config.py:VALID_VARIANTS` +
`VALID_THEMES`. The XP threshold curve stays fixed — only rendered
names + ELO interpolation change, so existing totals never trigger
retroactive level-ups.

## Scheduler — macOS (launchd)

File: `~/Library/LaunchAgents/com.local.claude-coach.plist`
Label: `com.local.claude-coach`
Schedule: daily at 04:00 local
Runs: `~/.claude/coach/bin/run-insights.sh` (wrapper sets minimal PATH)
Logs: `/tmp/claude-coach.log`, `/tmp/claude-coach.{out,err}`

```bash
# Trigger a run now (testing, doesn't wait for 04:00)
launchctl kickstart gui/$(id -u)/com.local.claude-coach

# Job state + last exit code
launchctl print gui/$(id -u)/com.local.claude-coach | head

# Pause the schedule
launchctl unload ~/Library/LaunchAgents/com.local.claude-coach.plist
```

## Scheduler — Linux (cron)

```bash
crontab -l                                     # view current entries
crontab -e                                     # edit
# Entry to add:
0 4 * * * $HOME/.claude/coach/bin/insights.sh 1d >> /tmp/claude-coach.log 2>&1
# Remove by deleting the line.
```

## Health + observability

There is no dashboard. Check these by hand when investigating:

```bash
# Last /coach-insights run result (tail last line):
tail -1 ~/.claude/coach/changelog.md

# All commits to the coach repo (profile mutations):
git -C ~/.claude/coach log --oneline | head

# Current profile state:
cat ~/.claude/coach/profile.yaml | head -80

# Scheduler state (cooldowns, pending completions):
cat ~/.claude/coach/.tip_state.json | python3 -m json.tool

# Session bank ledger:
cat ~/.claude/coach/banked_sessions.json | python3 -m json.tool | head -30

# macOS: cron/launchd logs:
tail -30 /tmp/claude-coach.log

# Run /coach status inside Claude Code:
/coach status
```

## Security

- **Two analysis paths, two network surfaces.**
  - *Deterministic cron path* (run-id `insights-<ts>`): local-only, no
    network calls. `analyze.py` reads redacted transcripts and feeds
    `merge.py`; nothing leaves the machine.
  - *Weekly LLM-driven path* (run-id `insights-weekly-<ts>`):
    `insights-llm.sh` invokes `claude -p "/insights"` once per 7 days
    purely for the side effect of refreshing
    `~/.claude/usage-data/facets/*.json`. The CLI's stdout/stderr is
    discarded; `aggregate_facets.py` reads only the local sidecars
    Anthropic wrote. The `claude -p` call is the sole outbound surface
    in Coach, and it routes through the Claude Code CLI the user
    already runs — Coach itself never opens a socket, never uploads
    `profile.yaml`, and never transmits redacted or raw transcript
    content. Set `COACH_INSIGHTS_LLM_SKIP_REFRESH=1` to skip the LLM
    trigger entirely if you prefer fixture-only operation.
- **Secret redaction gate.** `redact.py` strips API keys (Anthropic /
  OpenAI / AWS / GitHub / Slack / Stripe / HuggingFace / npm / Google),
  Bearer tokens, PEM blocks, JWTs, and long hex strings *before* any
  downstream read.
- **Profile content.** `profile.yaml` stores only pattern slugs, counts,
  timestamps, and short redacted example strings — never raw transcript
  text, never code, never prompts.
- **Hook failsafes.** Both hooks are wrapped top-to-bottom in
  `try/except` and always exit 0. A coach bug cannot crash your Claude
  Code session.
- **Atomic writes.** Profile updates go through `tempfile +
  os.replace` under `fcntl.flock` — no partial-write corruption.
- **Bounded-wait locks.** `bank.py` waits up to 30s for the profile lock
  when `/coach-insights` is mid-run, then bails cleanly (transcript is still
  on disk, will bank on next `SessionStart`).

## Runbook — common operations

**Deploy a coach update to your machine:**
```bash
cd coach-claw && git pull && ./install.sh
```

**Bootstrap the profile from your last 7 days (deterministic — local, zero-token):**
`~/.claude/coach/bin/insights.sh 7d`

**Bootstrap from an LLM-driven re-read (on-demand, spawns `claude -p`):**
Inside Claude Code: `/coach-insights`

**Trigger the daily cron manually:**
macOS: `launchctl kickstart gui/$(id -u)/com.local.claude-coach`
Linux: `~/.claude/coach/bin/insights.sh 1d`

**Trigger the weekly LLM-driven path manually:**
```bash
~/.claude/coach/bin/insights-llm.sh --force            # full run
~/.claude/coach/bin/insights-llm.sh --force --dry-run  # print detections, skip merge
```
The wrapper invokes `claude -p "/insights"` to refresh facets sidecars,
aggregates them via `aggregate_facets.py`, and merges. `--force`
overrides the 7-day throttle the SessionStart hook honors.

**Check the weekly throttle state:**
```bash
ls -la ~/.claude/coach/.last_weekly_insights         # mtime → last run
# Force the next session to re-fire by aging the marker:
touch -d "8 days ago" ~/.claude/coach/.last_weekly_insights
# Then start a new Claude Code session — the SessionStart hook
# spawns insights-llm.sh detached. Wait ~90s for the merge.
```

**Check why a tip didn't fire:**
```bash
ls ~/.claude/coach/.disabled                  # exists? → disabled
cat ~/.claude/coach/.tip_state.json           # cooldowns? pending completions?
git -C ~/.claude/coach log -1                 # last /coach-insights run — recent?
```

**Check why the statusline ELO stopped moving:**
```bash
python3 -c "import json; d=json.load(open('$HOME/.claude/coach/banked_sessions.json')); \
  print('\n'.join(f\"{v['xp']:>3} raw → {v['banked']} banked  {v['at']}\" \
  for v in list(d.values())[-10:]))"
```
If recent sessions bank `0` (< 10 raw XP each), lifetime isn't growing
— expected behavior, not a bug. See README §XP mechanics.

**Reset coach state but keep binaries:**
```bash
rm ~/.claude/coach/profile.yaml ~/.claude/coach/banked_sessions.json \
   ~/.claude/coach/.tip_state.json ~/.claude/coach/.level_state.json
cp coach-claw/coach/profile.yaml ~/.claude/coach/profile.yaml
# Next /coach-insights run will rebuild the watch-list from scratch.
```

**Run the test suite:**
```bash
python3 -m pip install --user pytest
# From the bundle (full suite — install tests run here):
python3 -m pytest coach/tests/
# …or from the live install (3 install tests intentionally skip,
# because install.sh isn't copied into the deployed coach/ dir):
cd ~/.claude/coach && python3 -m pytest tests/
```
~3.5s either way.

**Total removal:**
`/coach uninstall` inside Claude Code; if that fails, see §Uninstall
above for the manual path.
