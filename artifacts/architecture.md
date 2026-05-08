# Architecture — Coach Claw

Reference for how the pieces fit together. For install/ops steps see
[`infrastructure.md`](./infrastructure.md). For tunables, troubleshooting,
and design rationale see [`../README.md`](../README.md).

## Overview

A passive coaching overlay for Claude Code. Reads the user's local session
transcripts on a schedule, detects behavior patterns (deterministically,
no LLM), maintains a watch-list in `profile.yaml`, and injects ambient
tips into Claude's context at runtime via two Claude Code hooks. A
gamification layer (XP / level ladder / ELO / streaks / graduations)
closes the feedback loop so the user can *see* themselves improving.

### Distribution model

Coach Claw ships in two parallel forms that share `~/.claude/coach/` state:

```
┌──────────────────────────────────────────────────────────────────┐
│  npm CLI  (canonical, provider-agnostic)                          │
│  install.sh, install-launchd.sh, npm/coach-claw.js                │
│  Patches ~/.claude/settings.json (hooks + statusLine).            │
│  Registers ~/Library/LaunchAgents/com.local.claude-coach.plist.   │
│  pip install --user pyyaml.                                       │
└──────────────────────────────────────────────────────────────────┘
                             │ writes / consumes
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Shared state dir:  ~/.claude/coach/                              │
│  profile.yaml, banked_sessions.json, .pending_*, .git/,           │
│  .user_config.json (path-injectable via COACH_CONFIG_DIR).        │
└──────────────────────────────────────────────────────────────────┘
                             ▲ writes / consumes
                             │
┌──────────────────────────────────────────────────────────────────┐
│  Claude Code plugin  (parallel; marketplace-distributed)          │
│  plugin/.claude-plugin/plugin.json                                │
│  plugin/skills/{coach,coach-insights,config,switch}/  (namespaced)│
│  plugin/hooks/hooks.json → bootstrap.sh + Python entry points     │
│  plugin/bin/  (bundled python core, mirrored from coach/bin/)     │
│  ${CLAUDE_PLUGIN_DATA}/venv/  → per-plugin PyYAML venv            │
│  Self-installs ~/.claude/settings.json:statusLine on first run.   │
│  Defers to npm CLI hooks if both are detected (coexistence_check).│
│  Distributes via rm0nroe/coach-claw-plugin-marketplace (GitHub).  │
└──────────────────────────────────────────────────────────────────┘
```

The npm CLI is the canonical distribution: it owns OS-side bits the
plugin can't reach (launchd/cron registration, main statusLine slot,
PyYAML pip install). The plugin is a Claude-Code-native alternative
that self-bootstraps PyYAML in its own venv and self-installs the
statusLine via SessionStart. Both surfaces share the same coach state,
the same Python core, and the same hook entry points (bundled into
both via `tools/build_plugin.py`). When both are installed, the
plugin's `coexistence_check.py` detects CLI hook entries in
`settings.json` and defers — running `/coach-claw:switch` flips
control to the plugin.

- **Where it runs**: the user's local machine only. Profile, transcripts,
  and detections stay in `~/.claude/coach/`. Coach itself does not
  independently upload transcripts. The weekly + on-demand
  `/coach-insights` path invokes `claude -p "/insights"` as a side-
  effect dependency to refresh local `facets/*.json` sidecars; that
  nested call is an Anthropic-side Claude Code operation running
  inside the user's existing authenticated session — Coach reads only
  the local sidecars Anthropic writes alongside.
- **Who it's for**: individual Claude Code users; safe to share with a
  team (see README §At a glance).
- **Platform**: macOS (launchd) + Linux (cron). Python 3.8+, PyYAML.
- **Claude API usage**: **zero direct API calls from Coach.** The
  daily cron path is pure Python (no LLM). The weekly path's nested
  `claude -p` call is metered the same as any other Claude Code
  session — billed by Anthropic, not by Coach.

## Technology decisions

| Choice | Why |
|---|---|
| Python 3.8+ | Standard on macOS/Linux, minimal deps, `fcntl` available for flock |
| PyYAML | Human-editable profile format, stable schema evolution |
| Shell (bash) for `insights.sh` | Fastest cron startup; Python-wrapped date math for portability |
| Claude Code SessionStart + UserPromptSubmit hooks | Two-phase injection: ambient watch-list once per session, live scheduler per prompt |
| `fcntl.flock` for concurrency | OS-native, crash-safe, no daemon required |
| Git repo at `~/.claude/coach/` | Every `/coach-insights` run is a commit → free rollback |
| Deterministic analyzer (`analyze.py`) | No LLM on the cron path → zero scheduled cost + reproducible |
| Pre-computed reward line, LLM writes body | Body stays specific to current work; numbers can't drift across machines |
| 50-level ladder + ELO rating | Familiar mental model (chess); 1000-2800 keeps 4-digit feel at every rank |
| Session → lifetime bank at 10:1 | Prevents a single session dominating lifetime; graduations (+5) stay dominant |

## System architecture

```mermaid
graph TD
    subgraph "User's machine"
      direction TB

      subgraph sched["Scheduled — daily (deterministic)"]
        direction LR
        CRON[launchd / cron] --> INS[insights.sh]
        INS --> ANALYZE[analyze.py\nstructural detect]
        INS --> REDACT[redact.py\nscrub secrets]
        INS --> SKILL_INV[skill_inventory.py\ninstalled skills]
        ANALYZE --> REDACT
        REDACT --> MERGE[merge.py\nconfidence+debounce+graduation]
        SKILL_INV --> MERGE
        MERGE --> PROFILE[(profile.yaml)]
        MERGE --> CHLOG[(changelog.md)]
      end

      subgraph weekly["Weekly — LLM-triggered (every 7d)"]
        direction LR
        WSTART[SessionStart hook\n.last_weekly_insights stale?] -->|spawn detached| WLLM[insights-llm.sh]
        WLLM -->|claude -p /insights| FACETS[(~/.claude/usage-data/facets/*.json)]
        WLLM --> AGG[aggregate_facets.py\nthreshold + cap]
        FACETS --> AGG
        AGG --> MERGE
      end

      subgraph live["Live — every session"]
        direction LR
        TRANSCRIPT[(Claude Code\ntranscripts\n~/.claude/projects/)]
        CC[Claude Code] -->|SessionStart| SSH[coach-session-start.py]
        CC -->|UserPromptSubmit| UPS[coach-user-prompt.py]
        SSH -->|spawn detached| BANK[bank.py\nsession xp → lifetime]
        BANK --> TRANSCRIPT
        BANK --> LEDGER[(banked_sessions.json)]
        BANK --> PROFILE
        UPS --> TRANSCRIPT
        UPS --> MARKERS[(.pending_* markers)]
        SSH -->|additionalContext| CC
        UPS -->|additionalContext| CC
        UPS --> TIPSTATE[(.tip_state.json)]
      end

      subgraph status["On demand"]
        CC_SL[Claude Code statusline] --> SL_WRAP[default-statusline-command.sh]
        SL_WRAP --> DSL[default_statusline.py\nmodel + bar prefix]
        DSL --> STATS[stats.render_segment\nlevel + ELO + ↑N]
        STATS --> PROFILE
        STATS --> TRANSCRIPT
        STATS --> UCFG[(.user_config.json)]
        USER[/coach status] --> STATUS[status.py]
        STATUS --> PROFILE
        CFG_CMD[/config slash command] --> UCFG
      end
    end

    sched -.->|writes| PROFILE
    live -.->|reads| PROFILE
```

## Component deep-dives

### `coach/bin/analyze.py` — deterministic pattern detector (cron path)

Reads a list of transcript JSONL paths, runs each through `redact.py` in a
subprocess (secret scrubbing), extracts structural signals (tool-use
counts, timing, presence of planning artifacts, test-run vs commit
ratios), aggregates across sessions, and emits detections as JSON.

This is **only used by the cron path** (`insights.sh`). The on-demand
`/coach-insights` skill delegates to Claude Code's built-in `/insights`
instead of running `analyze.py`.

**No LLM call.** Six hand-written pattern detectors:

| Pattern id | Trigger |
|---|---|
| `under-planning` | 5+ edits within 120s of first user turn without TaskCreate/TodoWrite/ExitPlanMode first |
| `edits-without-testing` | 10+ edits, zero test-runner invocations in session |
| `commit-without-testing` | `git commit` after 5+ edits with zero test runs |
| `heavy-agent-delegation` | 8+ `Agent` tool spawns in one session |
| `exploration-without-landing` | 15+ Reads, zero Edits/Writes, 10+ assistant turns |
| `skipped-search-tools` | 20+ Reads with ≤2 Grep/Glob calls |

Three of these (`edits-without-testing`, `commit-without-testing`,
`exploration-without-landing`) emit an explicit `reward_hint` so merge.py
doesn't have to infer one.

### `coach/bin/redact.py` — secret scrubber

Runs before `analyze.py` touches a transcript. Regex-based scrubbing of:
Anthropic / OpenAI / AWS / GitHub / Slack / Stripe / HuggingFace / npm /
Google API keys, Bearer tokens, JWT, PEM blocks, long hex strings, and
`FOO_KEY=…` / `FOO_SECRET=…` / `FOO_TOKEN=…` style env-var assignments. Output goes to
stdout (in-memory stream to analyze.py); original transcript is never
mutated.

### `coach/bin/merge.py` — profile math

Takes a detections JSON list + the current profile and applies:

- 2-of-3 debounce (candidate → probationary)
- 7-day probationary window (probationary → active)
- Absence graduation at `clean_streak_runs >= 5` (weakness retires)
- Presence graduation at `positive_run_streak >= 5` (strength masters)
- Confidence decay 0.05/day since last seen
- Bounded cap (10 active entries; lowest `confidence × priority` evicted)
- Mid-streak rewards (+1/+1/+1/+2 on streaks 1–4, both directions)
- Regression revocation (re-detection of a graduated negative pattern)
- Skill hints snapshot (installed skills minus in-window used)
- Atomic write via tempfile + `os.replace` under `fcntl.flock`

All mutations emit markers (`.pending_graduation`, `.pending_streak_rewards`,
`.pending_regression`) that the `UserPromptSubmit` hook surfaces as
in-chat banners on the next turn.

### `coach/bin/bank.py` — session→lifetime XP

Invoked as detached subprocess by `SessionStart`. Scans transcripts older
than `COOLDOWN_MINUTES=30` (session considered done), skips already-banked
sessions (checks `banked_sessions.json`), delegates scoring to
`scoring.py`, converts raw session XP to lifetime at 10:1, writes to
profile + ledger atomically. Uses bounded-wait `flock` (30s) to handle
races with `/coach-insights`.

### `coach/bin/scoring.py` — transcript scorer

Single source of truth for action detection + XP math. Shared by
`stats.py`, `bank.py`, and the hook (via imports). Baseline actions
always score (`test_run +2`, `commit +1`, `skill_invoke +1` for unique
skills); dynamic actions declared via `reward_hint` on profile entries
score additionally (no double-counting with baseline).

### `coach/bin/stats.py` — coach statusline segment

Reads profile + current transcript (via parsed payload), computes
lifetime XP (graduated×5 + max_streak + banked), uses **hybrid math** so
the rating stays live without phantom level-ups:

- `level_xp = lifetime + session // 10` → drives level index, level-up
  detection, name (integer only — matches bank.py)
- `progress_xp = lifetime + session / 10` → drives sigil color % +
  ELO within-level slide (float — ticks on every test/commit)

Public surface is split:
- `render_segment(payload: dict | None) -> str` — pure-return path
  consumed in-process by `default_statusline.py`. Empty string when
  the coach has nothing to show (no profile + no transcript signal).
- `main()` — thin CLI wrapper around `render_segment(_read_stdin_json())`
  for direct `python3 stats.py` invocations.

Rendered (via the variant selected in `.user_config.json`): e.g.
`◆ Ⅱ 1051 Iterator ↑5` (crystal default) — sigil (bronze→diamond by
within-level %), Roman numeral, 4-digit ELO (1000–2800 linear by
default, configurable via `/config elo`), level name, bank-projected
session gain. Full lineup at `themes.py` + `statusline_variants.py`.

### `coach/bin/default_statusline.py` — rich statusline composer

Trampolined to from `coach/default-statusline-command.sh` (a 2-line
bash script that resolves its own directory via `${BASH_SOURCE%/*}`,
so it works under any `CLAUDE_DIR`). Reads the Claude Code statusline
JSON from stdin once, renders three segments separated by `┃`:

1. `◆ <model>` — lowercased, ` context` stripped, spaces → `·`
2. 20-segment context-window bar with white→cobalt 24-bit RGB gradient
3. `stats.render_segment(payload)` — coach segment per user's variant

No external dependencies — replaces an earlier wrapper that used `jq`
twice (and broke on macOS, which doesn't ship jq). Pure bash parameter
expansion in the trampoline + pure Python everything else.

### `coach/bin/themes.py` — 12 rank-name ladders

Each theme is a list of 50 unique single-word level names ordered
L1 → L50. Themes only change rendered names; XP threshold curve stays
fixed. Lineup: `craft` (default), `forge`,
`cosmic`, `ocean`, `skyrim`, `marvel`, `dc`, `finalfantasy`,
`military`, `lotr`, `starwars`, `hacker`. Pop-culture themes use only
public-domain mythology, real historical titles, and genre-generic
terminology — no franchise-coined neologisms, no named characters.
Brand-safety regression guard:
`test_themes.py:test_pop_culture_themes_exclude_franchise_coined_words`.

### `coach/bin/statusline_variants.py` — coach-segment renderers

Five variants, all consuming the same `Glyphs` payload (level, name,
ELO, session XP, sigil tier, within-level pct). Output shapes:

| Variant | Sample |
|---|---|
| `crystal` (default) | `◆ Ⅶ 1232 Virtuoso ↑15` |
| `pips` | `●●●○○○○○○○ Virtuoso ↑15` |
| `bracket` | `[Ⅶ Virtuoso] 1232 ↑15` |
| `slash` | `L7 / Virtuoso / 1232 ↑15` |
| `forge` | `⚒ Virtuoso · L7 · 1232 ↑15` |

Dispatch via `render(name, glyphs)`. Canonical visual contract pinned
per variant in `test_statusline_variants.py`.

### `coach/bin/user_config.py` — `/config` storage

Atomic read/write of `~/.claude/coach/.user_config.json`. Schema v1
keys: `statusline_variant`, `theme`, `elo_min`, `elo_max`. Single
source of truth for valid sets is the `VALID_VARIANTS` and
`VALID_THEMES` constants in this file — slash-command docs and
schema validation both read from here. Defaults: `crystal` + `craft`
+ `1000-2800`. Missing file → defaults; invalid value for any field
→ falls back to default for that field. Reads never raise.

Path resolution honors `COACH_CONFIG_DIR` env var (falls back to
`~/.claude/coach`) and is recomputed per call so the npm wrapper —
which sets `COACH_CONFIG_DIR=$CLAUDE_DIR/coach` when `CLAUDE_DIR` is
non-default — can route writes to a custom install location without
patching the module. PEP 562 `__getattr__` exposes `CONFIG_PATH` as a
fresh-each-access alias for back-compat with external readers.

### `coach/bin/configure.py` — `coach-claw config` entrypoint

Argparse-based CLI dispatched by `npx coach-claw config <set|preview|wizard>`.
The npm wrapper (`runConfig` in `npm/coach-claw.js`) `spawnSync`s
`python3 ~/.claude/coach/bin/configure.py` with args verbatim and
propagates `CLAUDE_DIR → COACH_CONFIG_DIR` so the script writes to the
matching install location.

- **`set`** — flag-driven non-interactive write. Calls
  `user_config.update()` so unspecified keys stay at their existing
  values. Validation surfaces from `user_config.save()` as exit 1 +
  one-line stderr.
- **`preview`** — byte-equivalent to `/config preview` from inside
  Claude Code (same `Glyphs` payload, same `render()` dispatch, same
  ladder slice).
- **`wizard`** — TTY-gated interactive picker (variant + theme;
  ELO is power-user-only via `set --elo MIN MAX`). Default-on-Enter,
  number-or-name input, validation re-prompt with a 3-try cap,
  `KeyboardInterrupt` → exit 0 with "Wizard cancelled — no changes
  saved." Non-TTY invocations print a pointer to `set` and exit 0.

### `coach/bin/status.py` — `/coach status`

Comprehensive state breakdown. Imports `LEVELS` from `stats.py` (single
source of truth for the ladder). Renders lifetime breakdown, session
breakdown, weakness/strength tables with per-pattern streak bars
(`●●●·· 3/5`) sorted descending by streak, and a tail of the last
`/coach-insights` run.

### `coach/bin/skill_inventory.py` — installed-skills scan

Scans `~/.claude/skills/` directories for SKILL.md files, reads their
YAML frontmatter (description + optional `projects:` scope) via
`yaml.safe_load`, subtracts skills used in the `/coach-insights` window, and
emits a JSON array that `merge.py` stores under
`profile.yaml:skill_hints`. Each hint carries a `projects` list:

- **Frontmatter `projects: [...]`** is authoritative when the key is
  present at all — `projects: []` is honored as an explicit "this
  skill is cross-project / global" declaration and is NOT re-tagged
  by inference.
- **Frontmatter absent** (`fm.get("projects") is None`) → infer scope
  from the rolling `skills_by_project` accumulator
  (`{project: {skill_id: count}}`) passed in via
  `--skills-by-project`. Threshold-2 rule (`INFER_THRESHOLD = 2`):
  ≥2 invocations in exactly one project → tag with that project;
  ≥2 projects observed → graduate to global (`[]`); otherwise
  cold-start (`[]`, no scope claim yet).

### `coach/bin/analyze.py` — per-project skill aggregation

Beyond the six pattern detectors above, `analyze.py` also emits a
per-session `skills_by_project` mapping
(`{project: {skill_id: count}}`) alongside the flat `skills_used`
counter. The small-window path no longer short-circuits this — even
n<3 windows produce the per-project breakdown so /coach-insights re-runs
on small datasets still feed the inference signal.

Project name is derived from the Claude Code transcript directory
slug via `_project_name_from_slug`; hyphenated original project names
collapse to one anchor token (e.g. `acme-app` → `app`). Skills
with the wrong inferred project can be pinned with explicit
frontmatter.

### `coach/bin/insights.sh` — deterministic insights pass (cron path)

Invoked by `launchd` on macOS / `cron` on Linux. Cross-platform date math
(via Python, not BSD `date -v`) + PATH-resolved `python3` (not
`/usr/bin/python3` hardcode). Pipes transcripts → `analyze.py` →
`merge.py` → auto-git-commit. Bypasses the claude CLI entirely, so the
daily run is zero-token and deterministic. The on-demand
`/coach-insights` skill is the LLM-driven counterpart that delegates
to Claude Code's built-in `/insights` — see §`/coach-insights` skill.
The script's filename stays `insights.sh` for launchd-plist stability.

### `coach/bin/insights-llm.sh` — LLM-triggered weekly pass

The sibling pipeline to `insights.sh`. Fires once per 7 days
(throttled via `~/.claude/coach/.last_weekly_insights` mtime).
Triggered by the SessionStart hook when stale, or manually via
`/coach-insights` (which calls the script with `--force`).

Pipeline:

1. Generate `RUN_ID="insights-weekly-$(date -u +...)"`. The
   `insights-weekly-` prefix is the only discriminator from the daily
   path — both feed the same `merge.py`.
2. Spawn `COACH_DISABLE=1 claude -p "/insights"` (300s soft timeout)
   to refresh `~/.claude/usage-data/facets/*.json` sidecars. The CLI's
   stdout is **discarded** — we run it for the side effect on disk
   only. `COACH_DISABLE=1` keeps the nested session from loading
   Coach hooks (which would contaminate the analysis).
3. Pipe the facets dir through `aggregate_facets.py`.
4. Hand the resulting detections JSON to `merge.py` with
   `--run-id "$RUN_ID"`. Same atomic-write + flock + changelog.
5. `git commit` + `touch .last_weekly_insights`.

**Fail-hard, both stages.** Three "bail before merge" gates protect
against false-clean evidence passes that would advance absence-based
streaks on phantom data:

- **LLM-step fail-hard (exit 6).** If `claude` isn't on PATH, exits
  nonzero, or times out (`COACH_INSIGHTS_LLM_TIMEOUT`, default 300s),
  the wrapper exits 6 BEFORE aggregation. Without this, the wrapper
  would fall through to aggregating stale-or-empty facets, which
  `merge.py` would treat as a clean week — silently advancing
  absence-based streaks on phantom evidence.
- **Aggregator fail-hard (exit 5).** If `aggregate_facets.py` exits
  nonzero or emits unparseable JSON, the wrapper exits 5 — same
  reasoning, one stage later in the pipeline.
- **No-evidence gate (exit 7).** If `claude -p` succeeded but the
  refreshed facets dir contains zero sessions in the requested
  window (`n_sessions == 0`), `aggregate_facets.py` returns its
  `EXIT_NO_EVIDENCE = 3` and the wrapper translates to its own
  exit 7. Asymmetry pinned by tests: empty detections WITH
  `n_sessions > 0` is a valid clean week and merges normally;
  empty detections WITH `n_sessions == 0` is no evidence and
  bails. None of the three gates touch `.last_weekly_insights` —
  the next session retries from scratch.

**Test seam.** `COACH_INSIGHTS_LLM_SKIP_REFRESH=1` bypasses the
subprocess; `COACH_FACETS_DIR=<dir>` overrides the source dir;
`COACH_DIR_OVERRIDE=<dir>` redirects profile/marker/lock paths.

### `coach/bin/aggregate_facets.py` — facets → detections

Pure-Python aggregator that reads `~/.claude/usage-data/facets/*.json`
sidecars (mtime-windowed), tallies stable enum keys across sessions,
applies threshold rules, and emits a detections JSON list to stdout.

Threshold rules (mirror `analyze.py`):

| Source key | Detection direction | Threshold |
|---|---|---|
| `friction_counts.<key> ≥ 1` | negative | ≥25% of sessions |
| `primary_success == <key>` | positive | ≥60% of sessions |

ID derivation: `_` → `-` (e.g. `misunderstood_request` →
`misunderstood-request`). No prose translation, no slug
canonicalization, no fuzzy matching — facets enum keys are stable
kebab/snake-case slugs by Anthropic's data contract, so the input
already matches Coach's id shape.

Examples are pulled from each session's `friction_detail` /
`brief_summary` field, deduped, capped at 3 × 120 chars, and redacted
of file paths and file extensions before storage in `profile.yaml`.

Detections are sorted by ratio descending and capped at 8 per run.
Schema-validated: drop entries without `id` or with
`direction ∉ {negative, positive}`.

### `skills/coach-insights/SKILL.md` — on-demand insights pass

A thin wrapper around `insights-llm.sh --force`. The `--force` flag
overrides the 7-day throttle the SessionStart hook honors, so a
manual run always does work. The skill captures the wrapper's stdout
and reports the run-id + detection summary back to the user.

Manual and auto-spawned weekly paths share the same logic by
construction — they cannot diverge.

Frontmatter carries `disable-model-invocation: true` — the skill
mutates profile state and creates a git commit, so Claude is blocked
from invoking it implicitly from intent. Only an explicit
`/coach-insights` from the user fires it.

**Privacy asymmetry vs the cron path.** The weekly pipeline triggers
an Anthropic-side LLM step (the `/insights` analysis), which the user
is already authorized for by virtue of running Claude Code. Coach
reads only the locally-written sidecar JSON, never the CLI's content
output. `examples` strings written to `profile.yaml` are redacted of
file paths and file extensions before storage.

**Cost.** Each weekly run spawns one `claude -p` subprocess. We don't
pay for output tokens we never read — the CLI's content output is
discarded — so the trigger costs ~$0.01–$0.03/week, ~$0.50/year.

### `hooks/coach-session-start.py`

- Spawns `bank.py` in a detached subprocess (fire-and-forget)
- Loads `profile.yaml` + `skill_hints`, builds a watch-list of up to
  `MAX_INJECTED=5` entries above `MIN_CONFIDENCE=0.30`
- Calls `detect_render_env()` to choose terminal vs IDE rendering rules,
  then emits a `<coach>` additionalContext block (~2.4K tokens) with the
  appropriate FORMAT instructions
- Failsafe `try/except` everywhere; always exits 0

### `coach/bin/render_env.py` — terminal vs IDE detection

Single source of truth for which markdown shape coach output uses.
Reads `CLAUDE_CODE_ENTRYPOINT` (the env var Claude Code sets to identify
its own surface — `cli`, `vscode`, `claude-vscode` (Cursor), `jetbrains`,
`mcp`, `sdk-py`, `sdk-ts`, `ide-onboarding`). Allowlist semantics:
known IDE entrypoints get `"ide"`; everything else (cli, mcp, sdk,
unknown) defaults to `"terminal"`. Honors `COACH_RENDER_ENV={ide,terminal}`
override for testing. Both hooks call it once in `main()` and thread the
result through every `_block`/`_banner`/`_attribution` function.

Two render shapes:

- **Terminal**: `> *Label:* …` blockquote with italic attribution lines
  (`_↑ +N …_`). Themes render blockquotes dim/gray, giving the coach
  its signature visual weight in terminal Claude Code.
- **IDE**: HR-framed (`---` top + bottom) with bold leader (`🦞 **Label**`)
  and inline-code-span pills for metadata (`` `↑ +N per …` ``). IDE chat
  panels render `---` as sharp horizontal rules and inline code as
  badge-styled pills, but render blockquotes with no visible styling and
  do not support GFM admonitions (`> [!TIP]`) — verified empirically
  against Cursor's chat panel 2026-05.

### `hooks/coach-user-prompt.py`

Deterministic tip scheduler. On every `UserPromptSubmit`:

1. Check cooldowns (`TIP_GLOBAL_COOLDOWN_SEC=300`,
   `TIP_PER_TIP_COOLDOWN_HOURS=24`)
2. Roll `TIP_FIRE_PROBABILITY=0.35`
3. Read current transcript, compute
   `_session_signal() → (signal, project_anchors)`. `signal` is
   tokens from user messages + recent tool-uses + cwd path;
   `project_anchors` is the union of cwd's last path component and
   the nearest `.git`-rooted directory name (`_find_git_root_name`,
   stops at `$HOME`).
4. Build candidate pool from profile entries + skill_hints, filter
   skill hints by `_skill_fits_session()`. **Two gates run in order:**
   - **Project-scoped gate** (skill has `projects: [...]`): must
     match a `project_anchors` token. Out-of-project → skip
     regardless of overlap. Missing anchors → skip (conservative).
     In-project → still require some topic overlap (with
     project-name tokens stripped, to prevent circular matches).
   - **Untagged gate** (skill has empty `projects`): thin signals
     (<3 tokens) drop skills entirely; otherwise pass iff ≥2
     "distinctive" overlap tokens (not in `_COMMON_DEV_VOCAB`) OR
     one anchor-token match. Single distinctive token is NOT
     enough — a shared `mobile` or `ssh` token spans unrelated
     projects in practice.
   - Escape hatch: `COACH_ALL_SKILLS=1` bypasses both gates.
5. Weighted pick: `confidence × priority × tier_multiplier × streak_urgency`
   with `MIN_SKILL_SHARE=0.25` floor
6. Pre-compute reward attribution lines, env-shaped: terminal returns
   italic lines (`_↑ +N …_` + separate staged streak line such as
   `_🌡️ warming up …_` / `_🦍 strength mastered …_`); IDE returns
   inline-code-span pills with the same content.
   Skill labels carry the 🦞 Coach Claw persona; weakness/strength labels
   are plain `*Tip:* / *Pointer:* / …` in terminal, `🦞 **Tip** / …` in
   IDE (every IDE label gets the 🦞 persona prefix via `_ide_label`).
7. Also: detect completions for previously-fired tips (scan transcript
   for the reward_hint action), emit `<coach-tip-complete>` banner
8. Also: read `.pending_*` markers via `_assemble_celebrate_block` —
   collapses same-pattern streak entries to the highest streak, drops
   ticks whose id graduated in the same batch, prefixes a catch-up
   line when any marker `created_at` predates today, then emits the
   `<coach-celebrate>` block as **pre-rendered verbatim banners** (not
   template-fill — names, direction-correct shape, body sentence all
   resolved in Python; the model just reproduces).
9. Emit `<coach-tip>` block with REQUIRED-render instruction

Failsafe `try/except`; always exits 0.

## Data model

### `profile.yaml`

```yaml
schema_version: 1
updated: "2026-04-20"
banked_session_xp: 42            # cumulative banked lifetime XP
recent_runs: ["run-id-1", "run-id-2", "run-id-3"]   # last DEBOUNCE_WINDOW=3
entries:                         # active + probationary + candidate
  - id: edits-without-testing
    name: edits without testing
    tier: active                 # candidate | probationary | active
    direction: negative          # negative (weakness) | positive (strength)
    confidence: 0.82
    priority: 4                  # 1-5, tiebreaker on cap eviction
    nudge: "…"
    examples: ["session abc123: 10 edits, 0 tests"]
    first_seen: "2026-03-12"
    last_seen_in_run: "2026-04-19"
    last_fired: "2026-04-18T22:15:00+00:00"
    promoted_at: "2026-03-19"
    source_runs: [run-ids…]
    source_session_ids: [session-hashes…]
    total_occurrences: 14
    clean_streak_runs: 3         # absence streak (→ weakness retirement)
    positive_run_streak: 0       # presence streak (→ strength mastery)
    reward_hint:                 # what user action completes a tip
      action: test_run           # test_run | commit | skill_invoke | doc_write
      xp: 2
      description: "test run (pytest / jest / cargo test / …)"
graduated:                       # retired weaknesses + mastered strengths
  - id: over-mocks
    direction: negative
    graduated_at: "2026-04-10"
    graduated_reason: "absent-5-runs"
    final_tier: active
    total_occurrences: 22
skill_hints:                     # installed-but-unused skills
  - id: <your-skill-id>
    description: "…"
    short_tip: "…"
    projects: []                 # [] = global; ["widget"] = project-scoped
                                 # (frontmatter authoritative; otherwise inferred
                                 #  from skills_by_project history)
skills_by_project:               # rolling per-project invocation accumulator
  widget:                        # additive — every /coach-insights run sums in
    cli-migrate: 7          # ≥2 invocations in 1 project → tag the
    widget-build: 4             # skill with that project; ≥2 projects → []
  service:
    deploy-staging: 3
```

### `banked_sessions.json`

```json
{
  "<session-uuid>": {
    "xp": 15,
    "banked": 1,
    "at": "2026-04-20T00:57:36+00:00",
    "transcript": "/Users/…/<uuid>.jsonl"
  }
}
```

### Marker files (single-shot, consumed by hook)

| File | Shape | Rendered as |
|---|---|---|
| `.pending_graduation` | `{"graduations": [ {id, name, direction, …} ]}` | 🎓 banner |
| `.pending_streak_rewards` | `{"rewards": [ {id, streak, target, xp_awarded, direction} ]}` | ⚡️ banner |
| `.pending_regression` | `{"regressions": [ {id, name, …} ]}` | ⚠️ banner |
| `.pending_levelup` | `{"from", "to", "from_idx", "to_idx", "xp_at_levelup"}` | 🎉 banner |

### `.tip_state.json` (scheduler state)

Read/modify/write updates are serialized with a sibling
`.tip_state.json.lock` flock and persisted via tempfile + `os.replace`.

```json
{
  "last_global_fire": "ISO",
  "last_fired": {"<tip_id>": "ISO"},
  "pending_completions": {
    "<tip_id>": {
      "fired_at": "ISO",
      "spec": {"action": "test_run", …},
      "kind": "weakness",
      "entry_id": "edits-without-testing",
      "clean_streak": 3,
      "acknowledged": false,
      "acknowledged_at": null
    }
  }
}
```

## XP / leveling model

- Baseline scoring: `test_run +2`, `commit +1`, `skill_invoke +1` (unique)
- Session cap: **15 raw**
- Session → lifetime bank: **10:1** (`session // 10`)
- Lifetime XP = `graduated_count × 5 + max_clean_streak + banked_session_xp`
- Level ladder: **50 levels**, thresholds `[0, 3, 8, 15, 25, 40, 60, 90, …]`
  with +5/level after L8, landing L50 at 5,865 XP
- ELO: linear **1000 → 2800** across the ladder with within-level slide
- Mid-streak rewards: `+1/+1/+1/+2` across streak 1–4 (both directions)
- Graduation: `+5` lump (weakness retires / strength masters at streak 5)
- Regression: if a graduated negative pattern is re-detected, revoke
  graduation and reinsert as probationary (force re-earning)

## Tip scheduler behavior

```mermaid
sequenceDiagram
    participant User
    participant CC as Claude Code
    participant Hook as coach-user-prompt.py
    participant Profile as profile.yaml
    participant Transcript as transcript.jsonl

    User->>CC: prompt submit
    CC->>Hook: stdin payload + transcript_path
    Hook->>Profile: load
    Hook->>Transcript: scan recent tool-uses
    Hook->>Hook: build session_signal (tokens)
    Hook->>Hook: check cooldowns + roll 35%
    Hook->>Hook: build pool (filter skills by session_signal)
    Hook->>Hook: weighted pick (tier × streak × confidence × priority)\n  with 25% skill-share floor
    Hook->>Hook: pre-compute reward attribution lines
    Hook->>CC: additionalContext: &lt;coach-tip&gt; MUST-render instruction
    CC->>User: renders tip at end of response
    User->>CC: does the tracked action (e.g. pytest)
    User->>CC: next prompt
    CC->>Hook: stdin payload
    Hook->>Transcript: scan for action since fired_at
    Hook->>CC: additionalContext: &lt;coach-tip-complete&gt; ✅ banner
    CC->>User: renders ACK at top of next response
```

## External integrations

Only Claude Code itself — via the hook protocol, the skills loader,
and (on the weekly path) the `claude -p "/insights"` subprocess that
refreshes Anthropic-side `facets/*.json` sidecars. No third-party
services, no telemetry. The only outbound network traffic is the
Anthropic-side `/insights` step the user already authorized by
running Claude Code; Coach itself does not call out.

## File layout reference

```
~/.claude/                                    # live install
├── coach/
│   ├── profile.yaml                          # source of truth
│   ├── banked_sessions.json                  # session ledger
│   ├── changelog.md                          # /coach-insights history
│   ├── log.ndjson                            # nudge log
│   ├── .disabled?                            # flag: hooks silent
│   ├── .lock                                 # flock file
│   ├── .pending_*                            # hook-consumed markers
│   ├── .tip_state.json                       # scheduler state
│   ├── .level_state.json                     # level high-water mark (locked + atomic)
│   ├── .last_session_start                   # throttle for session events
│   ├── .user_config.json                    # /config storage (variant/theme/ELO)
│   ├── default-statusline-command.sh         # 2-line trampoline
│   ├── bin/*.py, *.sh                        # binaries (19 .py + 3 .sh)
│   └── tests/*.py                            # pytest suite
├── hooks/
│   ├── coach-session-start.py
│   └── coach-user-prompt.py
└── skills/
    ├── coach/SKILL.md                        # /coach command
    ├── config/SKILL.md                       # /config command
    └── coach-insights/SKILL.md               # /coach-insights command
```
