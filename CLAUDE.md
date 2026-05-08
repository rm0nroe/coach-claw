# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Coach Claw is a self-evolving coaching layer for Claude Code.
It reads the user's local session transcripts on a schedule, detects
behavior patterns deterministically (no LLM on the cron path), maintains
a watch-list of weaknesses and a reinforcement list of strengths in
`profile.yaml`, and injects ambient tips + XP rewards into Claude's
context at runtime via two Claude Code hooks. As the user's work
shifts, `/coach-insights` updates what's tracked — the coach grows with the
user, not against a fixed checklist.

- **Bundle root**: this directory (the cloned repo)
- **Live install target**: `~/.claude/` — hooks go under `hooks/`,
  coach binaries + data under `coach/`, slash commands under `skills/`
- **Target platform**: macOS (launchd) + Linux (cron). Python 3.8+.
- **Two analysis paths.** Daily deterministic cron is local-only,
  zero-network (analyze.py over redacted transcripts; run-id
  `insights-<ts>`). Weekly LLM-driven path triggers Claude Code's
  built-in `/insights` once per 7 days for the side effect of
  refreshing `~/.claude/usage-data/facets/*.json` sidecars, then
  aggregates them deterministically (run-id `insights-weekly-<ts>`).
  Both paths feed the same `merge.py`. `profile.yaml` stays local.

## Build & test commands

No compile step — pure Python + bash. Core workflows:

```bash
# Install to ~/.claude/ (idempotent, preserves existing profile data —
# also auto-recovers from a prior /coach uninstall when no live coach/ dir
# exists but a coach.bak.<ts>/ sibling does. Default-on prune trims old
# .bak.<ts> to the 3 most recent of each kind.)
./install.sh
./install.sh --seed              # …or seed profile from last 7 days of transcripts
./install.sh --fresh             # …or skip recovery (force a true fresh install)
./install.sh --no-prune-backups  # …or keep ALL .bak.<ts> (forensic / recovery use)

# macOS daily Coach insights pass (deterministic cron — kickstarts the first run immediately)
./install-launchd.sh

# Run the full test suite
python3 -m pip install --user pytest                         # one-time
cd ~/.claude/coach && python3 -m pytest tests/               # live install (runtime only)
python3 -m pytest coach/tests/ tests/plugin/                  # bundle (full suite incl. plugin)
python3 -m pytest coach/tests/                                # bundle (runtime/CLI only — fastest)
python3 tools/build_plugin.py                                 # rebuild plugin/ from canonical sources

# Smoke-test hooks without running Claude Code
echo '{}' | python3 ~/.claude/hooks/coach-session-start.py
echo '{}' | python3 ~/.claude/hooks/coach-user-prompt.py

# Trigger the deterministic insights pass manually (window in days)
~/.claude/coach/bin/insights.sh 1d

# Trigger the weekly LLM-driven path manually (--force overrides 7-day throttle)
~/.claude/coach/bin/insights-llm.sh --force          # full run
~/.claude/coach/bin/insights-llm.sh --force --dry-run # print detections, skip merge

# Inspect scheduler state
cat ~/.claude/coach/.tip_state.json | python3 -m json.tool

# Roll back one /coach-insights run
git -C ~/.claude/coach checkout HEAD~1 -- profile.yaml
```

## Architecture

```
 Daily deterministic cron                Live session (every turn)
 (run-id: insights-<ts>)
                                         SessionStart hook
 analyze.py (deterministic)                 ├─ spawns bank.py
   └─ redact.py (pre-read)                  ├─ stale-check spawns
 skill_inventory.py                         │   insights-llm.sh
        │                                   └─ loads watch-list
        ▼
 merge.py ──────────────────▶  profile.yaml ◀─── UserPromptSubmit hook
        ▲                          (atomic            ├─ scheduler
        │                          write, git         ├─ completion
 aggregate_facets.py                commit per run)   └─ marker banners
   (deterministic, reads
   /insights facets/*.json)                       default_statusline.py
        ▲                                         status.py (/coach status)
 insights-llm.sh                                  user_config.json (/config)
   └─ claude -p "/insights"
      (side-effect only —
       stdout discarded)
 Weekly LLM-triggered path
 (run-id: insights-weekly-<ts>)
```

Full detail: `artifacts/architecture.md`.

## Key patterns

- **Determinism where it matters.** `analyze.py` + `merge.py` are
  LLM-free so cron runs cost zero tokens and are reproducible. The LLM
  only writes the tip *body* — the hook pre-computes label, XP line,
  and pick.
- **Shared modules for scoring/inference.** `scoring.py` + `reward_hints.py`
  are single sources of truth consumed by stats/bank/hook. Never
  duplicate an action detector or keyword heuristic — extend the
  shared module.
- **Atomic writes under flock.** Every profile mutation goes through
  `tempfile + os.replace` under `fcntl.flock`. See
  `merge.py:atomic_write_yaml` for the pattern.
- **Hook failsafes are non-negotiable.** Both hooks wrap main() in
  `try/except` and always exit 0. A hook crash must never break a
  Claude Code session.
- **Per-environment render shape.** Coach output branches by detected
  surface — terminal Claude Code uses a `> ` blockquote shape (themes
  render it dim/gray); IDE chat panels (VS Code, Cursor, JetBrains via
  `CLAUDE_CODE_ENTRYPOINT`) use an HR-framed shape with bold + 🦞 +
  inline-code-span pills, since blockquotes render with no visible
  styling and GFM admonitions are not supported in IDE chat WebViews.
  Detection lives in `coach/bin/render_env.py`; both hooks call
  `detect_render_env()` once in `main()` and thread `env` through every
  `_block`/`_banner`/`_attribution` function. Override with
  `COACH_RENDER_ENV={ide,terminal}`. See
  `coach-user-prompt.py:_streak_reward_block` etc. for the branch shape.
- **Celebrate banners are pre-rendered verbatim, not template-fill.**
  `_streak_reward_block`, `_graduation_block`, `_regression_block`,
  `_levelup_block` emit final banner markdown — names, direction-
  correct shape (mid-streak: positive→`↑`, negative→`↓`; ceremonies:
  positive→MASTERED 🌟, negative→GRADUATED ⚡️), and body sentences
  are resolved in Python before the model sees them. The
  `<coach-celebrate>` block instructs "render verbatim — do NOT
  re-interpret labels, swap directions, or substitute slugs for
  names." When adding a new banner kind, render the final markdown in
  the hook and pin it with a literal-text test; do NOT pass a
  `<placeholder>` template and trust the model.
- **Read-time dedup + graduation suppression for queued markers.**
  `.pending_streak_rewards` accumulates across `/coach-insights` runs
  (markers append, never coalesce — see `marker_io.atomic_marker_rmw_append`),
  so the consumer in `_assemble_celebrate_block` projects the list
  before rendering: (a) collapse same-pattern entries to the highest
  streak, (b) drop streak entries whose id graduated in the same batch.
  The +5 mastery banner alone carries the news. Don't add dedup at the
  producer — read-time keeps the fix robust against in-flight markers
  and concurrent writers.
- **Catch-up framing for predates-today markers.** If a consumed
  marker's *oldest unconsumed entry* was written on a calendar date
  earlier than `now`, the celebrate block prefixes a "Milestones
  earned across earlier sessions" line so queued banners don't look
  like they fired off the user's current command. Driven by
  `_marker_predates_today(payload, now)`, which reads the
  `oldest_entry_at` field (preserved across appends in
  `marker_io.atomic_marker_rmw_append`). Top-level `created_at` resets
  on every append (drives the 24h read-side TTL); `oldest_entry_at`
  does not (drives catch-up). Two timestamps, two purposes — don't
  collapse them.
- **Hybrid ELO math.** Level index + level-up detection use integer
  `lifetime + session // 10`; ELO within-level slide uses float
  `lifetime + session / 10`. This is load-bearing — keep them
  separate. See `stats.py:_compute_hybrid`.
- **Markers are per-session-consumed, not deleted on first read.**
  `/coach-insights` writes `.pending_*` files with `created_at` + `consumed_by`
  fields; the `UserPromptSubmit` hook calls `_read_and_consume(path,
  session_key, now)` which appends the session_key to `consumed_by` so
  each concurrent Claude Code session sees the marker exactly once.
  Markers self-clean after `MARKER_TTL_HOURS` (24h) to bound abandoned
  state. The read-modify-write is serialized via a sidecar flock. Never
  assume a marker has been deleted just because you've seen it — check
  TTL or `consumed_by` membership.
- **Session-relevance filtering for skills only.** Behavior-pattern
  entries (weaknesses/strengths) are always eligible; skill hints are
  filtered by `_skill_fits_session()` because the installed-skill
  catalog is often off-topic for the current session.
- **Two-gate skill filter: project scope first, then topic overlap.**
  `_skill_fits_session()` checks `skill_hint["projects"]` first. If
  the skill is project-scoped (frontmatter `projects: [...]` OR
  inferred-from-history via `skill_inventory.py:_infer_projects`), it
  must match a `project_anchors` token from cwd's last component or
  the nearest git-root dir name (`_find_git_root_name`, which stops
  at `$HOME`). Untagged skills fall through to the older overlap
  gate (≥2 distinctive tokens or one anchor-token match). Escape
  hatch: `COACH_ALL_SKILLS=1` bypasses both gates. See
  `coach-user-prompt.py:_skill_fits_session`.
- **Coach Claw 🦞 = skill-tip persona; ↑/↓ = the reward/streak marker.**
  Skill tips render with the canonical label `*🦞 From Coach Claw:*`
  (or plain `*Coach:*`); weakness/strength tips never use 🦞. The
  reward attribution line is `_↑ +N …_` for positive progress and
  `↓ ... -N` where a negative-direction marker is retiring.
  🪛 is intentionally not in the label/emoji pool. Pinned by
  `test_hook_relevance.py` branding-contract tests.
- **`/config` decouples display from math.**
  `~/.claude/coach/.user_config.json` (read at every render via
  `coach/bin/user_config.py`) carries `statusline_variant` + `theme`
  + `elo_min/elo_max`. `stats.py` reads it once at module import to
  populate `LEVELS`/`ELO_MIN`/`ELO_MAX`/`STATUSLINE_VARIANT`. The
  XP threshold curve stays fixed across themes so existing totals
  never trigger retroactive level-ups; only rendered names + ELO
  interpolation change. Single source of truth for valid sets:
  `VALID_VARIANTS` + `VALID_THEMES` constants in `user_config.py`
  — never duplicate them into a docstring or skill prompt.
- **Default statusline is pure Python, no external deps.**
  `coach/default-statusline-command.sh` is a 2-line trampoline
  (`exec "@PY@" "${BASH_SOURCE%/*}/bin/default_statusline.py"`).
  `default_statusline.py` parses the JSON payload, renders model +
  20-segment context-window bar, then composes
  `stats.render_segment(payload)` in-process. No `jq`, no `dirname`,
  no `cd` — pure bash parameter expansion in the wrapper, pure
  Python everything else. Keeps the installer dependency-free on
  macOS (which doesn't ship `jq`).
- **Run-id prefix is the path discriminator.** Daily deterministic
  cron emits `insights-<ts>` run-ids; weekly LLM-driven path emits
  `insights-weekly-<ts>`. `merge.py` is run-id agnostic (same atomic
  write, same flock, same changelog) — the prefix is purely a
  downstream discriminator visible in `coach/changelog.md`,
  `profile.yaml.recent_runs`, and per-entry `source_runs`. Streak
  math (`clean_streak_runs`, `positive_run_streak`) is intentionally
  agnostic too: a weakness that disappears in BOTH paths over the
  same window earns its graduation slightly faster than one tracked
  through either path alone (~14% acceleration upper bound).
- **Weekly /insights LLM call is a side-effect dependency, not a
  data source.** `insights-llm.sh` invokes
  `COACH_DISABLE=1 claude -p "/insights"` purely so Anthropic-side
  aggregation refreshes `~/.claude/usage-data/facets/*.json`. The
  CLI's stdout/stderr is discarded. `aggregate_facets.py` reads the
  JSON sidecars Anthropic wrote, applies threshold rules, emits
  detections. NEVER add prose translation, slug canonicalization,
  HTML parsing, or fuzzy slug matching to this path — the inputs are
  stable kebab-case enum keys by Anthropic's data contract. To test
  against fixture facets without spawning a real `claude -p`, set
  `COACH_INSIGHTS_LLM_SKIP_REFRESH=1` and point `COACH_FACETS_DIR` at
  the fixture dir.
- **Weekly wrapper holds `.weekly_insights.lock` for the entire
  run.** `insights-llm.sh` re-execs itself through
  `coach/bin/run_with_lock.py` at the very top (before arg parse)
  so the lock is held across the whole `claude -p` + aggregate +
  merge window. This is load-bearing: two SessionStart hooks racing
  on a stale throttle marker would both run the LLM and both merge
  without it. The lock helper exits `10` on contention; the wrapped
  bash sees `COACH_LLM_LOCK_HELD=1` in env and skips the re-exec.
  Throttle recheck happens AFTER the lock is acquired so the second
  wrapper picks up the first's just-touched marker. When extending
  this script, do NOT add work BEFORE the re-exec block — anything
  there runs unprotected.
- **Three bail-before-merge gates, all fail-hard.** The weekly path
  has three independent gates that exit BEFORE merge and BEFORE
  touching `.last_weekly_insights`. Merging on any of them would
  commit a clean-evidence pass that didn't happen, prematurely
  advancing absence-based streak counters and consuming the weekly
  cadence. Distinct exit codes for log observability:
  - **LLM-step fail-hard → exit 6.** If `claude` is missing from
    PATH, exits nonzero, or times out (`COACH_INSIGHTS_LLM_TIMEOUT`,
    default 300s). Pinned by `test_missing_claude_bails_before_merge`,
    `test_claude_nonzero_exit_bails_before_merge`, and
    `test_claude_timeout_bails_before_merge`. NEVER reintroduce
    fall-through here — the symmetry with the aggregator gate below
    is load-bearing.
  - **Aggregator fail-hard → exit 5.** If `aggregate_facets.py`
    returns nonzero or emits unparseable JSON. Pinned by
    `test_aggregator_failure_bails_before_merge` and
    `test_aggregator_garbled_output_bails_before_merge`.
  - **No-evidence gate → exit 7.** When `claude -p` succeeds but the
    facets dir contains zero sessions in window,
    `aggregate_facets.py` returns its `EXIT_NO_EVIDENCE = 3` and the
    wrapper translates to exit 7. Critical asymmetry: empty
    detections WITH `n_sessions > 0` is a clean week and merges
    normally; empty detections WITH `n_sessions == 0` is no
    evidence. Pinned by `test_no_sessions_in_window_returns_3`,
    `test_session_with_no_detections_still_exits_0` (the asymmetry
    pinpoint), and `test_no_evidence_bails_before_merge`.

## Critical gotchas

- **`mktemp` template Xs must be at the end.** `mktemp /tmp/foo-XXXXXX.json`
  on BSD (macOS) creates a file with literal `XXXXXX` in the name —
  later runs silently fail because it already exists. Use
  `/tmp/foo-XXXXXX` (no suffix) and the caller can treat the content
  as JSON regardless.
- **Claude Code hooks run in non-interactive shells.** Shell `.zshrc` /
  `.bashrc` profile-scoped PATH additions are NOT in scope. The hook
  command in `settings.json` must use the absolute path to `python3`
  resolved at install time — `./install.sh` does this via
  `command -v python3`. If the user changes Python installs, re-run
  the installer.
- **Shell scripts must use `python3` from PATH, Python subprocess calls
  must use `sys.executable`.** Never hardcode `/usr/bin/python3` — it
  breaks on Homebrew, pyenv, and Linux.
- **BSD `date -v-` vs GNU `date -d`.** Don't write date arithmetic in
  shell. Move it into a Python heredoc — see `insights.sh` for the
  pattern.
- **Transcript reads are rate-limited by size.** Don't `.read_text()`
  a full transcript; read line-by-line with `json.loads(line)` in a
  try/except and `continue` on bad lines. Transcripts can be tens of
  MB and contain malformed JSONL from earlier Claude Code versions.
- **`clean_streak_runs` vs `recent_runs` for graduation.** The
  graduation path MUST key off `entry["clean_streak_runs"]`, not a
  loop over `recent_runs`. `recent_runs` is capped at
  `DEBOUNCE_WINDOW=3`, so a loop over it can never reach
  `RETIRE_AFTER_ABSENT_RUNS=5`.
- **Manual `insights.sh` re-runs double-count `skills_by_project`.** The
  rolling accumulator in `profile.yaml` is additive and the analyzer is
  pure (same transcripts → same delta), so re-running inside the same
  window adds the same counts again. Production cron is fine because
  successive runs cover non-overlapping windows. If you re-run by hand
  to test changes, expect the per-project counters to climb — roll
  back with `git -C ~/.claude/coach checkout HEAD~N -- profile.yaml`.
- **Bank.py must wait, not fail, on lock contention.** `/coach-insights` can
  hold the profile lock for a second or so during merge. If bank.py
  bails immediately on lock contention, the session's XP never banks.
  Use `_acquire_lock_bounded()` (30s ceiling).
- **Fabricating tips is a bug.** Coach tips are strictly hook-gated.
  If no `<coach-tip>` block is in the current turn's context, stay
  silent — don't invent one to fill the slot. The hook's presence is
  the only authorization to render; without it, the coach is silent.
- **Schema version field is load-bearing.** `profile.yaml` carries
  `schema_version: 1`. When shape changes, ADD a migration function
  to `merge.py:load_profile()` — don't silently break old profiles.
- **Git log in ~/.claude/coach is authoritative for profile history.**
  Profile mutations commit per `/coach-insights` run. Rollback via
  `git checkout HEAD~1 -- profile.yaml` is the intended UX; don't
  break it by deleting `.git/`.
- **`stats.py` module globals are populated at import time from live
  user config.** `LEVELS`, `ELO_MIN`, `ELO_MAX`, `STATUSLINE_VARIANT`
  are set once at line ~120 by `_load_runtime_config()` reading
  `.user_config.json`. Tests that pin against the canonical craft
  ladder (e.g. `test_stats_hybrid.py`) MUST monkeypatch these globals
  to defaults via an autouse fixture — otherwise running
  `python3 -m pytest coach/tests/` after the user has run
  `/config theme <other>` fails 4 hybrid-math tests because L2 is no
  longer "Iterator". See `test_stats_hybrid.py:13-30`. Same rule if
  you add new tests that hardcode level names or ELO values.

## Deployment

1. Fresh install: `./install.sh` from the bundle. Idempotent.
2. macOS daily Coach insights cron: `./install-launchd.sh`. Launches `insights.sh` at 04:00.
3. Linux daily Coach insights cron: `crontab -e`, add:
   `0 4 * * * $HOME/.claude/coach/bin/insights.sh 1d >> /tmp/claude-coach.log 2>&1`
4. Upgrade: re-run `./install.sh`. Backs up to `.bak.<ts>`, restores
   `profile.yaml`, `banked_sessions.json`, `changelog.md`,
   `.user_config.json`, scheduler state files, and any `.pending_*`
   markers (full preserve list at `install.sh:218-230`).
5. Uninstall: `/coach uninstall` inside Claude Code, or see
   `artifacts/infrastructure.md` §Uninstall for manual steps.

## When adding a new feature

1. **Will it change `profile.yaml` shape?** Update `schema_version` and
   add a migration in `merge.py:load_profile()`. Add a test.
2. **Will it affect scoring?** Extend `coach/bin/scoring.py`:
   `ACTION_DETECTORS` dispatch + `BASELINE_ACTIONS` table if baseline.
   Add to `reward_hints.py:_HEURISTIC` if it's an inferrable action.
3. **Will it change the tip shape?** Update the `RENDER SHAPE` block
   in `coach-user-prompt.py:_render_tip_instructions()` AND add a
   regression test in `tests/test_hook_relevance.py` or a new test file.
4. **Will it change the statusline?** Extend `stats.py:_compute_hybrid`
   and add a test in `tests/test_stats_hybrid.py`.
5. **Will it change what a coach banner says?** Update the
   `_…_block(...)` function in `coach-user-prompt.py` AND the fallback
   rendering rules in the SessionStart `<coach>` block.
6. **Edit in the bundle, not live.** Source-of-truth is the bundle
   (this repo). Run `./install.sh` to push changes to `~/.claude/`.
   Never edit `~/.claude/hooks/*.py` or `~/.claude/coach/bin/*.py`
   directly — those are the install target, not the source.
7. **Run the test suite.** From the bundle:
   `python3 -m pytest coach/tests/ tests/plugin/` (full suite — runtime
   + plugin-build artifact tests). From the live install:
   `cd ~/.claude/coach && python3 -m pytest tests/` (runtime only;
   plugin/ doesn't exist there). The two test trees are split by
   directory so neither needs `skipif` markers — `tests/plugin/` is
   structurally absent in the live-install layout.
8. **If you touched `coach/bin/*.py` or `hooks/coach-*.py`, rebuild
   the plugin payload.** `tools/build_plugin.py` mirrors the canonical
   sources into `plugin/bin/` and `plugin/hooks/`. The
   `tests/plugin/test_synced.py` gate fails CI if you forget.

## Reference documents

- **`README.md`** — user-facing overview, install, cost, privacy,
  tunables, full troubleshooting.
- **`artifacts/architecture.md`** — component deep-dives, data model,
  mermaid diagrams.
- **`artifacts/infrastructure.md`** — deployment, service inventory,
  runbook.
- **`BACKLOG.md`** — candidate future work.
- **`coach/README.md`** — operator notes (lives next to the data at
  `~/.claude/coach/`).
- **`coach/tests/`** — runtime pytest suite (CLI + plugin-shared
  modules). See `coach/tests/test_*.py`.
- **`tests/plugin/`** — plugin-build artifact tests (manifest schema,
  skill namespacing, `tools/build_plugin.py` sync gate, bootstrap
  venv lifecycle, marketplace catalog). Bundle-only.
- **`plugin/`** — Claude Code plugin distribution payload. Generated
  from `coach/bin/`, `hooks/`, and hand-authored skills under
  `plugin/skills/` by `tools/build_plugin.py`.
- **`marketplace/`** — standalone Claude Code marketplace catalog
  pointing at `plugin/` via `git-subdir`. Synced to a separate
  `rm0nroe/coach-claw-plugin-marketplace` repo via
  `tools/publish_marketplace.py`.
