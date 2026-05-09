# Backlog — Coach Claw

Candidate future work.

## Legend

- `[ ]` Pending
- `[~]` In progress

---

## Scheduler / tip quality

- `[ ]` Mid-streak XP inflation review. Path XP went from `+5`
  (graduation only) to `+10` (graduation + mid-streak ticks). Decide
  whether lifetime XP growth is now too fast for the 50-level ladder;
  if so, either reduce graduation XP (retroactively demotes existing
  graduated entries) or stretch later-level thresholds. Design call —
  requires operator input, not a mechanical fix.
- `[ ]` `/coach debug-last-turn` subcommand. When a contributor says
  "no tip fired, why?", there's no fast diagnostic — they have to
  cat `.tip_state.json` and reason about cooldowns + probability by
  hand. Add a mode that dumps: eligible pool → per-tip weights →
  cooldown status → rolled probability → reason the pick was skipped
  (or what was picked).
- `[ ]` Smarter session-relevance tokenization. The current filter
  uses a simple `[a-z0-9]{3,}` regex split + a small noise-word set +
  `_COMMON_DEV_VOCAB` stoplist for distinctive-overlap matching.
  False negatives for unusual filenames (e.g. `.mjs`, `.vue`,
  compound tokens) could drop legitimately-relevant skill hints.
  Consider adding a few more short-whitelist tokens (`mjs`, `vue`,
  `svelte`, `rb`, `ex`) and/or a bigram sweep.
- `[ ]` Parity-diff sentinel. When the daily deterministic detector
  and the weekly facets-aggregator disagree on the same week (e.g.
  daily flags `edits-without-testing` but weekly tags
  `wrong-approach` instead), surface as a `.pending_parity_drift`
  marker the user can review. The most interesting capability the
  hybrid enables; needs more design before shipping. Likely shape: a
  `coach/bin/parity_diff.py` invoked at the end of `insights-llm.sh`
  that compares the most recent `insights-<ts>` and
  `insights-weekly-<ts>` runs in `recent_runs` and writes a marker
  if their detection-id sets diverge significantly.
- `[ ]` Source-pinned streak math. Optional knob to count
  `clean_streak_runs` separately for `insights-<ts>` vs
  `insights-weekly-<ts>` so graduation can be sourced from one path
  rather than both. Currently mixed (~14% accelerated graduation in
  the worst case). Schema impact: per-entry
  `clean_streak_runs_by_source` map; bump `schema_version` to 2 with
  migration.

## Privacy / redaction

- `[ ]` Extend `redact.py` to catch variable-name heuristics: any
  assignment to a name containing `token`, `secret`, `password`
  (case-insensitive) should redact the value even without an
  explicit `FOO_KEY=` pattern. Current redactor is conservative by
  design; this closes a specific gap.

## Plugin distribution

- `[ ]` Plugin name shortening. Today plugin skills are namespaced
  `/coach-claw:coach`, `/coach-claw:coach-insights`, `/coach-claw:config`
  per Claude Code's mandatory plugin namespacing. Renaming the plugin
  to `cc` (or similar short prefix) yields `/cc:coach` etc., closer to
  the npm CLI's `/coach` ergonomics. Tradeoff: `cc` is less
  discoverable in marketplace listings and could collide with another
  plugin's claim. Worth weighing once the plugin has real adoption
  data — current namespaced commands work, just verbose.
- `[x]` ~~`/coach-claw:doctor` skill.~~ Shipped in v0.1.3
  (2026-05-09). Five read-only probes: plugin install (reads
  `installed_plugins.json`), coexistence marker, statusLine ownership
  (CLI vs plugin vs claimed), cron registration (reuses
  `cron_check.py`), venv health (PyYAML import check). Mutating
  `--remove-statusline` flag covers the documented uninstall-cleanup
  gap (Anthropic's plugin lifecycle doesn't auto-clean settings.json
  keys plugins added). 32 regression tests.
- `[x]` ~~Wrap-mode statusline + variant tuning.~~ Shipped in v0.1.4
  (2026-05-09). Plugin SessionStart and CLI `install.sh` auto-wrap a
  user's claimed statusLine command — original is preserved in
  `.statusline-wrap.json`, Coach segment appends to the user's output
  with trailing-aware separator handling. Manual-Coach pre-flight
  detects users (like Ryan's box) who already integrate Coach in
  their custom script and skips wrap with a sticky opt-out marker.
  `/coach-claw:doctor --wrap-statusline` / `--unwrap-statusline`
  control surface. Variant cleanup: pips 10→5 segments + tier color,
  slash adds ⚔ sigil and drops ELO, forge drops ELO, bracket
  removed (saved configs fall through to crystal). ~78 new
  regression tests across wrap, action module, doctor, hook,
  self-patch, install, switch, and variants.
- `[x]` ~~v0.1.4 teammate-review fixes.~~ Shipped in v0.1.5
  (2026-05-09). Three findings: (high) `/coach-claw:doctor
  --remove-statusline` was deleting `integrated-externally`
  statuslines (Ryan-style custom scripts that internally call Coach)
  because the safety guard at `doctor.py:413` only protected
  `claimed`; (medium) `_build_wrapper_command` and `_desired_entry`
  interpolated paths into command strings without `shlex.quote`,
  breaking any CLAUDE_DIR with spaces under bash's `shell=True`;
  (low) `install.sh` closeout banner still said "5 variants × 12
  themes" after bracket was dropped. All four fixes shipped with
  six new regression tests, including a `bash -c` execute guard on
  the auto-wrap install test that would have caught the medium
  finding pre-merge.
- `[x]` ~~v0.1.5 teammate-review follow-up.~~ Shipped in v0.1.6
  (2026-05-09). Same defect class as v0.1.5's medium finding,
  third instance: `switch_to_plugin.py:_rewrite_cli_wrap_to_plugin`
  built the rewrite-target command via raw f-string interpolation,
  reachable when `/coach-claw:switch` runs with a CLAUDE_PLUGIN_ROOT
  containing spaces. Missed in v0.1.5's symmetric sweep because
  that round only audited `_build_wrapper_command` + `_desired_entry`.
  Audited the rest of `coach/bin/` and found no other instances.
  Two new regression tests pinning the fix (`shlex.split` round-trip
  + `bash -c` exec guard).
- `[ ]` Plugin marketplace beta channel. v0.1.0-beta initial release
  ships from the marketplace's `main` branch. Add a `beta` branch
  with its own `marketplace.json:name` (e.g., `coach-claw-plugins-beta`)
  pointing at a `beta` ref of the source repo. Per the docs' release-
  channels pattern, users on the beta channel add a separate
  marketplace entry; this lets us iterate without disrupting the
  stable channel. Defer until the stable channel has organic users
  worth protecting.
- `[x]` ~~Slash commands bypass `bootstrap.sh`'s PyYAML venv setup.~~
  Fixed in v0.1.2 (commit `2766eb7`). New `plugin/bin/run.sh` is the
  skill wrapper; bootstrap delegates to it after its coexistence
  guard. SKILL.md files route Python through run.sh; insights-*.sh
  scripts wedge the venv into PATH on startup. 13 regression tests.
- `[ ]` Submit plugin to Anthropic's official marketplace. The
  rm0nroe/coach-claw-plugin-marketplace self-host is the path of
  least resistance for v0.1.0; submitting to the official marketplace
  via claude.ai/settings/plugins/submit unlocks `/plugin browse`
  discovery. Anthropic's review SLA is undocumented — wait until
  v1.0.0 stable, then submit.

## Installer / portability

- `[ ]` Linux cron install helper. Today Linux users have to
  `crontab -e` by hand. A one-liner `install-cron.sh` that detects
  existing coach entries, appends idempotently, and prints the same
  "job registered" message as the macOS installer would make the
  Linux story identical to macOS.
- `[ ]` Detect pyenv shims. If `command -v python3` resolves to a
  pyenv shim (`~/.pyenv/shims/python3`), that shim depends on the
  shell's `PYENV_VERSION` which may not be set inside Claude Code's
  non-interactive hook shell. Consider resolving to the actual
  interpreter via `pyenv which python3` at install time.
- `[ ]` Interactive seed prompt in `install.sh`. Today the installer
  is fully non-interactive: a fresh user runs `./install.sh` and
  lands with an empty profile, has to read the "Next steps" block,
  and decide whether to re-run with `--seed`. Add a TTY-gated prompt
  before the seed step (only when stdin is a tty AND `--seed` was
  not passed AND `~/.claude/projects/` exists with transcript data):
  "Seed profile from the last 7 days of your existing Claude Code
  transcripts now? [Y/n]" defaulting to Y. Non-tty runs (CI, piped,
  `< /dev/null`) preserve the current behavior. Also add `--no-seed`
  for users who want to suppress the prompt without piping. Bonus:
  same shape can ask about `install-launchd.sh` registration so
  first-run ends with a fully-configured cron, not a manual second
  step.
- `[ ]` `COACH_DISABLE_WEEKLY=1` opt-out. The weekly LLM step is
  fail-hard (`insights-llm.sh` exit 6) when `claude` is missing from
  PATH / exits nonzero / times out. Users on plans without
  `claude -p` access (or who removed the binary intentionally) see
  an exit-6 stderr line on every session start until the throttle's
  7-day window passes. Add an opt-out: `COACH_DISABLE_WEEKLY=1` in
  env, or a `weekly_disabled: true` flag in
  `~/.claude/coach/.user_config.json`, read by the SessionStart hook
  so the wrapper is never spawned for these users. Pair with a
  one-line note in the wrapper's exit-6 stderr ("set
  COACH_DISABLE_WEEKLY=1 to silence") for self-service.

## Testing

- `[ ]` Integration smoke test. The pytest suite covers units; add
  one e2e test that exercises `install.sh` in a throwaway
  `CLAUDE_DIR` sandbox, runs `insights.sh`, triggers a `SessionStart`
  hook, verifies profile + ledger get touched, then cleans up.
- `[ ]` Schema migration harness. `profile.yaml` has a
  `schema_version: 1` field but no migration logic. When v2 ships
  (field rename, shape change), add a `_migrate_profile(data)` step
  in `merge.py:load_profile()` that bumps the version idempotently.

## Docs

- `[ ]` Architecture diagram SVG in `artifacts/`. The mermaid block
  in `architecture.md` renders on GitHub but not everywhere; ship an
  SVG export for offline reading.
- `[ ]` Short demo GIF or screencast in the README. New users
  install, open Claude Code, and see... nothing (tips are 35%
  probability + cooldown gated). A ~30s screencast showing a
  graduation banner would cut adoption friction.

## Gamification

- `[ ]` Export lifetime stats. For contributors who want to compare
  progress, a `/coach export` that prints the JSON summary (level,
  XP breakdown, graduated patterns) without exposing the raw profile
  would enable opt-in peer visibility.
- `[ ]` Turn ambient tips into explicit coach quests. Instead of
  only random profile tips, maintain a small active quest set: one
  primary weakness to work on, one strength to reinforce, and one
  optional skill discovery quest. Each quest should have a clear
  trigger, completion condition, XP value, and streak impact. This
  would make the coach feel less like scattered advice and more
  like a personal training plan.
- `[ ]` Add daily/weekly recap mechanics. Summarize what improved,
  what regressed, what leveled up, and which habits are closest to
  graduation. Keep it local-only and profile-derived. Output could
  power `/coach recap`, the future UI, and a lightweight
  end-of-week "training report" without exposing transcript content.
- `[ ]` Add achievement badges for meaningful Claude Code habits.
  Examples: `Tested Before Commit`, `Plan First`, `Search Before
  Read`, `Small Batch Shipper`, `Skill Explorer`, `No-Rollback
  Discipline`, `Clean 5`. Badges should map to real behavior and
  avoid vanity awards that do not teach.
- `[ ]` Add XP balancing simulation. Build a small script that runs
  synthetic profiles through the level ladder, session banking,
  mid-streak rewards, graduations, and regressions. Use it before
  changing XP constants so the 50-level ladder stays motivating
  instead of too fast or too grindy.

## Product / coaching intelligence

- `[ ]` Add pattern-specific coach playbooks. Each detected pattern
  should have an optional playbook with: why it matters, what
  better behavior looks like, completion actions, anti-pattern
  examples, and suggested copy fragments. This keeps tips precise
  without making the hook rely on generic LLM judgment for every
  behavior.
- `[ ]` Add "next best habit" ranking. Compute the single
  highest-leverage habit for the user right now using severity,
  recurrence, current streak, confidence, and available completion
  actions. Surface it in `/coach status`, `/coach recap`, and the
  future UI.
- `[ ]` Add per-project coaching profiles. The same user may have
  different weaknesses in different repos. Store optional project
  buckets for behavior entries, not only skill hints, so the coach
  can distinguish "frontend app under-testing" from "backend library
  has strong test discipline."
- `[ ]` Add a feedback command for false positives and bad advice.
  A command such as `/coach feedback <pattern-id> wrong|not-now|good`
  should update a small local feedback ledger. Merge can use that
  ledger to reduce confidence, suppress tips for a cooldown, or
  preserve patterns the user finds useful.
- `[ ]` Add a capability map for "best Claude Code user" skills.
  Define the target curriculum explicitly: planning,
  search/navigation, safe editing, verification, git hygiene, skill
  use, delegation discipline, context management, documentation,
  and recovery. Map detections and achievements to this curriculum
  so progress teaches a complete craft, not just whatever regexes
  happen to exist.

## UI / dashboard

- `[ ]` Build a local Coach dashboard UI. A small local-only web UI
  should read `~/.claude/coach/profile.yaml`, `changelog.md`,
  `.tip_state.json`, and `banked_sessions.json` and render the
  user's level, XP sources, active weaknesses, strengths, streak
  bars, recent graduations, regressions, and next best habit. It
  should not read raw transcripts by default.
- `[ ]` Add a timeline view for the coaching loop. Show
  `/coach-insights` runs, detections, promotions, streak ticks,
  graduations, regressions, level-ups, and banked sessions as a
  chronological timeline. This would make the autonomous system
  inspectable and help users trust why the coach is saying what it
  says.
- `[ ]` Add a "why did this tip fire?" UI/debug panel. Display the
  eligible pool, current-session evidence, weights, cooldowns,
  random roll, selected tip, and completion spec. This can share
  logic with `/coach debug-last-turn` and would make scheduler
  tuning much easier.
- `[ ]` Add safe profile editing controls. Let users disable,
  rename, or annotate a pattern from the UI without hand-editing
  YAML. Every edit should go through a small command that validates
  schema, preserves git history, and records why the user changed
  it.
- `[ ]` Add UI export/share mode. Generate a redacted progress card
  or JSON export containing level, badges, graduated patterns, and
  aggregate stats, but no transcript snippets or project secrets.
  This supports opt-in social comparison without compromising the
  local-only privacy model.

## Nice-to-have polish

- `[ ]` Idempotency guard on `insights.sh` so manual re-runs don't
  double-count `skills_by_project`. Options: persist a
  `last_applied_run_id` (or max-transcript-mtime watermark) in
  `profile.yaml` and have `merge.py` skip the delta when the
  watermark hasn't advanced. Production cron is unaffected
  (non-overlapping windows); this is for hand-driven test runs.
- `[ ]` Bundle-side `Makefile` with targets `test`, `lint`,
  `install`, `clean`. Currently you run commands directly; a
  Makefile gives one canonical entry point for contributors.
