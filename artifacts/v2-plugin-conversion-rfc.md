# Coach Claw v2.0.0 — Plugin Conversion RFC (SUPERSEDED)

> Status: **superseded 2026-05-08**. The "v2.0.0 conversion" framing
> was rejected in favor of a parallel plugin distribution that lives
> alongside the npm CLI rather than replacing it. Implementation
> shipped under commits `06ffdaf`, `b69833b`, `d841d23`. Document
> retained as a historical record of the research that preceded the
> decision.
>
> See [Phase A–G plan](https://github.com/rm0nroe/coach-claw/blob/main/CLAUDE.md)
> for the actual implementation, plus `plugin/`, `marketplace/`, and
> `tests/plugin/` in the repo for the shipped artifacts.
>
> Original status: draft / pre-research.
> Author: Ryan + Claude (working session 2026-05-08).
> Target version: v2.0.0 (breaking change permitted).

---

## Context

Coach Claw v1.0.5 (locally shipped, npm publish blocked until 3pm Eastern 2026-05-09 due to npm's 24h republish blackout) is a **standalone Claude Code extension** delivered via an npm CLI (`@rm0nroe/coach-claw`). The CLI's `install` subcommand copies hooks, skills, and a statusLine into `~/.claude/` and patches `settings.json`.

In May 2026, Claude Code shipped a formal plugin model with `.claude-plugin/plugin.json` manifests, marketplace discovery, version pinning, and lifecycle management (see [code.claude.com/docs/en/plugins](https://code.claude.com/docs/en/plugins)). The plugin model is the right home for ~70% of Coach Claw's surface — slash commands, hooks, agent definitions — but doesn't cover three load-bearing pieces of the current design:

- The main `statusLine` (plugin `settings.json` only supports `agent` and `subagentStatusLine`)
- Daily insights cron (launchd / cron, OS-side, no plugin lifecycle hook)
- PyYAML runtime dependency (no auto-install path in the plugin model)

This RFC captures the **hybrid v2.0.0 shape** Ryan endorsed in the 2026-05-08 working session: convert to a Claude Code plugin where it fits, keep a thin npm CLI (or equivalent) for OS-side setup that the plugin model can't reach. **No features dropped.** The decision is which surfaces move and which stay external.

---

## Why v2.0.0 (and why not patch-on-top)

- **Breaking changes are required** to convert: skill names get namespaced (`/coach` → `/coach-claw:coach`), hook configuration moves out of `settings.json` patching into `hooks/hooks.json`, the install path changes from `npx @rm0nroe/coach-claw install` to `/plugin install coach-claw@<marketplace>`. None of these can ship as a minor version.
- Distribution becomes Claude-Code-native — the plugin marketplace replaces the npm install entry point as the user's primary install path. This unlocks `/plugin install`, version pinning, automatic updates, and removes the 90% of `install.sh` that exists to safely patch `settings.json`.
- Migration story is non-trivial — existing v1.x users have populated `~/.claude/coach/` state (profile.yaml, banked_sessions.json, per-run git history, `.pending_*` markers) that needs to survive the cutover. Worth a major-version line in the sand to do it cleanly.

---

## Hybrid v2.0.0 vision

```
┌────────────────────────────────────────────────────────────────┐
│ Claude Code plugin (.claude-plugin/plugin.json)                │
│                                                                 │
│  ├── skills/                                                    │
│  │    ├── coach/SKILL.md          → /coach-claw:coach           │
│  │    ├── coach-insights/SKILL.md → /coach-claw:coach-insights  │
│  │    └── config/SKILL.md         → /coach-claw:config          │
│  │                                                              │
│  ├── hooks/hooks.json             → SessionStart + UserPromptSubmit
│  │                                                              │
│  ├── bin/                         → coach Python modules        │
│  │    └── (analyze, merge, configure, stats, ...)              │
│  │                                                              │
│  └── monitors/monitors.json (?)   → maybe consume daily-cron    │
│                                     output as plugin events     │
└────────────────────────────────────────────────────────────────┘
                            │
                  reads / writes
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ ~/.claude/coach/  (user state, OUTSIDE the plugin install)      │
│                                                                 │
│  profile.yaml, banked_sessions.json, .pending_*, .git/, ...    │
│  .user_config.json (already path-injectable via COACH_CONFIG_DIR)│
└────────────────────────────────────────────────────────────────┘
                            │
              writes     consumes (cron, statusline)
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ Thin OS-side companion (CLI? script? ?)                         │
│                                                                 │
│  - Daily insights cron (launchd / cron registration)            │
│  - Main statusLine setup (settings.json patch — one-time)       │
│  - PyYAML preflight (or workaround)                             │
└────────────────────────────────────────────────────────────────┘
```

Three layers, clean separation:
- **Plugin** = the in-Claude-Code surface (skills, hooks). Versioned + distributed via marketplace.
- **State dir** = `~/.claude/coach/` — same as today, already env-var-overridable via `COACH_CONFIG_DIR` (Phase 2).
- **Companion setup** = the OS-side bits the plugin can't touch. Could be the npm CLI (kept alive), a one-shot install script, or whatever survives research.

Open question for next session: **what shape does the OS-side companion take?** npm CLI has the lowest user friction (already shipped) but stretches the "Claude Code plugin" framing. A `bash <(curl ...)` script is more self-contained but loses the npm version-pin story. TBD.

---

## Research items (each gets a deep-dive next session)

### Item 1 — Skill name namespacing

**Problem**: Plugin skills MUST be prefixed with the plugin name. So `/coach`, `/coach-insights`, `/config` become `/coach-claw:coach`, `/coach-claw:coach-insights`, `/coach-claw:config`. This is an explicit Claude Code design decision (per docs: "Plugin skills are always namespaced to prevent conflicts when multiple plugins have skills with the same name").

**Today's UX cost**: existing users have built muscle memory around `/coach status`, `/config preview`, etc. These shortcuts also appear in the v1.0.5 install banner, in the README, in error messages, and in the post-install verbal handoff. A v2.0.0 conversion breaks all of that.

**Research questions**:
- Is there a Claude Code feature for short aliases or top-level skill registration that bypasses plugin namespacing? Check `/help`, `~/.claude/settings.json` keys, and the plugins-reference docs.
- Can we register `/coach` etc. as user-level skills (in `~/.claude/skills/`) that delegate to `/coach-claw:coach`? This would be a hybrid: plugin ships the canonical implementation; standalone shim provides the short alias.
- Is the `name` field of `plugin.json` short-namable? E.g., naming the plugin `cc` (instead of `coach-claw`) yields `/cc:coach`. Tradeoff: name discoverability in marketplace listings.
- Does Anthropic's official marketplace tolerate short plugin names, or is there a naming convention?
- Is there an Anthropic RFC / GitHub issue tracking this UX gap?

**Possible paths (to be ranked)**:
1. **Accept namespacing** as the new normal. Update all docs to `/coach-claw:coach`. Provide a `/cc:coach` alias by also registering the plugin under a short name (if plugin manifests support aliases — needs research).
2. **Hybrid: plugin + standalone shim.** Plugin is the canonical implementation; install-time companion writes thin standalone skill files in `~/.claude/skills/coach/SKILL.md` that just say "delegate to plugin". Ugly but preserves short names.
3. **Two plugins**: a "core" plugin with the long name and a "shortcuts" plugin with `name: "cc"` that just re-exports. Probably overkill.
4. **Stay standalone** (don't convert to a plugin). Reject the conversion entirely. Keeps short names but loses marketplace discoverability — defeats the v2.0.0 thesis.

**Decision needed**: which path. Defer to next session.

---

### Item 2 — Main statusLine integration

**User direction (2026-05-08)**: "we will build a workaround for the statusLine issue or keep the cli alive if absolutely needed."

**Problem**: Coach Claw's main `statusLine` (the rich `◆ Ⅶ 1232 Virtuoso ↑15` line that replaces Claude Code's default model-name footer) is registered in `~/.claude/settings.json` under the top-level `statusLine` key. The plugin model's `settings.json` only supports `agent` and `subagentStatusLine` keys — not the main `statusLine` (per docs: "Currently, only the `agent` and `subagentStatusLine` keys are supported. ... Unknown keys are silently ignored.").

**What the plugin model gives us**: subagent statuslines (when an agent is active in the main thread, the agent's statusline can override). That's not the same use case — Coach's statusLine is meant to be the user's PRIMARY statusline, every prompt, agent or no.

**Research questions**:
- Confirm the plugin docs are current. Is there a backlog issue to add `statusLine` to plugin settings.json? If yes, what's the timeline? Worth waiting?
- Does Claude Code support multiple statuslines composed left-to-right, or is it a single-key override? (Affects whether plugin coexistence is easy.)
- Could a SessionStart hook patch `~/.claude/settings.json` from inside the plugin? (Self-installing statusLine.) What are the side effects when the plugin is uninstalled — does Claude Code clean up the patched settings.json automatically, or do we leave dead config behind?
- Could we render the statusLine via a different mechanism — e.g., emitting it as part of every assistant response via a hook, instead of via Claude Code's statusline slot? Tradeoffs: pollutes chat history, less pinned, but doesn't depend on the statusLine slot.
- Is there a community plugin that ships a main-statusline override? How did they solve it?

**Possible paths**:
1. **Companion sets statusLine once, plugin reads its own state.** OS-side companion (npm CLI or one-shot script) patches `~/.claude/settings.json:statusLine` exactly once at setup. Plugin doesn't touch settings.json. Tradeoff: setup step exists outside the plugin install — discoverability hit.
2. **SessionStart hook patches statusLine on first run.** Plugin's SessionStart hook checks `~/.claude/settings.json` for the Coach statusLine entry; writes it if absent. Self-healing on plugin install. Tradeoff: cross-plugin conflict (if another plugin also wants the statusLine slot, we'd race).
3. **Wait for plugin model to support `statusLine` key.** File an Anthropic RFC; defer v2.0.0 until shipped. Tradeoff: indefinite hold.
4. **Drop the rich statusLine.** Render the same info via assistant-response hook prefix or a `/coach status` snapshot. Tradeoff: loses the "always-visible level + ELO" UX which is one of Coach's signature touches.

**Decision needed**: probably path 1 or 2. Path 2 is more elegant if the cross-plugin race is solvable.

---

### Item 3 — Daily insights cron

**User direction (2026-05-08)**: "could we just keep ours and have our plugin read its results? otherwise, I'm sure we can build a workaround for this too because we need this just like we need 2."

**Problem**: The deterministic daily insights pass (`coach/bin/insights.sh 1d`) runs at 04:00 local via macOS launchd (`com.local.claude-coach.plist`) or Linux cron. This is OS-level scheduling — Claude Code's plugin lifecycle has no equivalent. The daily run is load-bearing: it analyzes new transcripts, updates `profile.yaml`, emits markers (`.pending_streak_rewards`, `.pending_graduation`, `.pending_regression`) that the SessionStart hook surfaces as in-chat banners on the next session.

**The plugin model's closest feature**: `monitors/monitors.json` runs a long-running command in the background (e.g., `tail -F ./logs/error.log`) and pipes each stdout line to Claude as a notification during a session. Different abstraction — not a cron, more like a streaming log tail.

**Research questions**:
- Confirm that `monitors/` doesn't support cron-like scheduling. Re-read [plugins-reference.md#monitors](https://code.claude.com/docs/en/plugins-reference#monitors). Is there a `when` trigger that supports time-based firing?
- Could `monitors/` be repurposed to *consume* the existing daily cron's output? E.g., the plugin's monitor runs `tail -F ~/.claude/coach/log.ndjson` and surfaces NEW entries as Claude notifications. Doesn't replace the cron itself but lets the plugin react to it.
- Do plugins have an "on session start" hook that could *check whether the cron is set up* and prompt the user to install it via the companion? (One-time setup nudge instead of a recurring cron from the plugin.)
- Are there security considerations to a plugin that registers OS-level scheduling — i.e., would Anthropic accept a plugin that ships a launchd plist installer to its marketplace?

**User's stated direction**: keep the existing launchd / cron path, plugin consumes its results. That's path A below — confirmed as the working assumption. Other paths listed for completeness in case path A turns out to have hidden blockers.

**Possible paths**:
1. **Keep launchd / cron unchanged. Plugin reads `~/.claude/coach/` state** (which the cron writes to). Companion has a `coach-claw schedule install` subcommand for one-time launchd registration. Plugin's SessionStart hook checks for the launchd plist + nudges if missing. *(User-endorsed path.)*
2. **Replace daily cron with `monitors/`** that runs `~/.claude/coach/bin/insights.sh 1d` on a timer (if monitors support timers). All insights happen inside Claude Code's lifecycle. Tradeoffs: only fires when Claude Code is open; loses background-while-away pattern.
3. **Drop daily cron entirely.** Rely only on the weekly `/coach-insights` LLM-driven path. Massive feature regression — daily cron is the deterministic, zero-cost, never-misses-a-day backbone.
4. **Hybrid: daily cron stays, plugin's monitor surfaces fresh insights as notifications.** Combines (1) with a `monitors/monitors.json` watcher on `coach/log.ndjson` so the user sees "fresh weakness detected" notifications without waiting for the SessionStart banner.

**Decision needed**: confirm path 1 + research path 4 as a possible add-on.

---

### Item 4 — Local mutable state

**User direction**: not flagged for explicit decision. Listed for completeness.

**Problem**: Coach maintains `~/.claude/coach/profile.yaml` (atomic write under flock), `banked_sessions.json` (XP ledger), per-run git history (commits per `/coach-insights`), `.pending_*` markers (per-session-consumed), `.tip_state.json` (scheduler state). The plugin model installs into a Claude-Code-managed location and may not provide a writable user-state directory at the same path.

**Working assumption**: state stays at `~/.claude/coach/` (or `$COACH_CONFIG_DIR/coach/`, since Phase 2 made the path injectable). The plugin's hooks read/write this location via the existing path-resolution helpers. The plugin's installed footprint is read-only code; the writable state lives outside.

**Research questions**:
- Confirm Claude Code plugins can read/write paths outside their own installed dir. (Almost certainly yes, since the entire MCP server pattern relies on this — but worth verifying for hooks specifically.)
- Does the plugin's installed dir get cleaned up on `/plugin uninstall`? If so, do we lose any code the user might have customized? (Probably fine since users shouldn't be editing live install per CLAUDE.md, but verify.)
- Does the plugin's installed dir get *backed up* during version bumps? (We don't have settings.json patching to manage anymore, but we DO need to ensure user state in `~/.claude/coach/` survives a plugin upgrade.)
- For a fresh install via `/plugin install coach-claw`, where does the user's state initially live? Do we need a "first-run init" hook that creates `~/.claude/coach/profile.yaml`?

**Possible paths**:
1. **State stays at `~/.claude/coach/` indefinitely.** Plugin's hooks call `_resolve_config_path()` (Phase 2 logic). `coach-claw setup` companion creates the dir on first install; plugin's SessionStart hook auto-creates if missing. No migration needed for v1.x users — same path.
2. **State moves to plugin-provided data dir** (if Claude Code exposes one). Migration script reads old `~/.claude/coach/` and copies to the new location. Higher risk; needs validation.

**Decision needed**: confirm path 1 (status quo, state path unchanged) — almost certainly the right answer.

---

### Item 5 — PyYAML runtime dependency

**User direction (2026-05-08)**: "we need to figure out workaround options here and research it further."

**Problem**: `coach/bin/merge.py`, `analyze.py`, `aggregate_facets.py`, and the hooks all `import yaml` to parse / write `profile.yaml`. PyYAML is a third-party C-extension package. Today `install.sh:112-124` auto-installs it via `pip install --user` with a PEP 668 (`--break-system-packages`) fallback for Homebrew Python 3.12+. The plugin model has no equivalent runtime-deps install step — plugins are loaded at startup; missing imports crash the hook (which then exits 0 via the failsafe but contributes nothing).

**Research questions**:
- Does the plugin model have ANY documented runtime-dep install hook? Re-read plugins-reference; check for `requirements.txt`-style support, post-install hooks, or first-run setup specs.
- Is there a `monitors/` or `bin/` mechanism we could repurpose to lazy-install PyYAML on first hook fire? (Seems hacky.)
- What does the Python community ship for "small YAML reader for known schemas"? Is `ruamel.yaml` smaller than PyYAML? Is there a stdlib-only path?
- Is `tomllib` (Python 3.11+) acceptable as a swap target? `profile.yaml` schema is YAML 1.1 with no advanced features (no anchors, no merge keys, no tags) — TOML is a feasible representation.
- What's the minimum Python version we want to require? Today: 3.8+. If we go TOML-via-stdlib, we'd need 3.11+.
- What's the install footprint of vendoring PyYAML? Could we ship a Cython-compiled wheel inline? Licensing OK (PyYAML is MIT)?

**Possible paths**:
1. **Replace PyYAML with stdlib JSON.** `profile.yaml` becomes `profile.json`. Loses YAML's commenting + readability (the schema today has inline comments documenting fields). Roundtrips cleanly; zero runtime deps. Migration: one-time conversion script.
2. **Replace PyYAML with stdlib `tomllib` + a small writer.** `profile.yaml` becomes `profile.toml`. TOML reads via stdlib (3.11+); no stdlib writer but `tomli_w` exists, ~150 lines if vendored. Schema fits TOML. Bumps Python floor to 3.11.
3. **Vendor a tiny YAML subset reader/writer.** Write ~100 lines of Python that parse the specific schema shape we use. Brittle to schema evolution.
4. **Bundle PyYAML inside the plugin's `bin/` dir.** Ship the wheel; have hooks use a vendored sys.path. Cross-platform wheel matrix (macOS arm64, macOS x86_64, Linux x86_64, etc.) is annoying — probably 20MB.
5. **Document PyYAML as a manual prereq.** Plugin's setup companion runs `python3 -m pip install --user pyyaml` once. Same as today's install.sh logic, just behind the companion subcommand. Zero refactor cost.
6. **Write profile.yaml via a tiny custom writer + read via PyYAML if available, fall back to a parser-of-our-subset.** Hybrid: full YAML output + lazy fallback for read. More code, complete robustness.

**Recommendation pending research**: probably path 1 (JSON) or path 2 (TOML). Both eliminate the dep entirely. Path 5 is the lowest-effort but doesn't actually solve the plugin distribution gap.

**Decision needed**: pick a path after researching the cost of each.

---

### Item 6 — Migration story for v1.x → v2.0.0

**Problem not yet covered**: Existing v1.x users have `~/.claude/coach/` populated with profile data, settings.json patched with hook commands pointing at `~/.claude/hooks/coach-*.py`, a launchd plist registered, possibly a `.user_config.json`. Upgrading to v2.0.0 (plugin) needs to:
- Preserve all of that state
- Remove the v1.x install footprint cleanly (the patched settings.json hooks point at `~/.claude/hooks/coach-*.py` which the plugin no longer ships from there)
- Not double-fire hooks (v1.x + v2.0.0 hooks both registered = dup tip injection)

**Research questions**:
- Can a `coach-claw migrate` companion command be the migration step? It runs once: detects v1.x install, removes settings.json hook entries, removes `~/.claude/hooks/coach-*.py`, removes `~/.claude/skills/coach/` etc., leaves `~/.claude/coach/` state untouched, then prompts user to `/plugin install coach-claw` from inside Claude Code.
- Does the plugin's first SessionStart hook detect v1.x leftovers and offer migration?
- How do we communicate the migration to existing users? Banner in the next v1.x release saying "v2 coming, run `coach-claw migrate` when ready"?
- Is there any chance v1.x and v2.0.0 hooks could coexist briefly (during the migration window) without doubling tips? Probably not — the SessionStart hook spawns bank.py once per session; running it twice would double-bank XP.

**Possible paths**:
1. **`coach-claw migrate` one-shot command.** Companion CLI ships a `migrate` subcommand that: stops the launchd cron, removes settings.json hook entries, deletes `~/.claude/hooks/coach-*.py` and `~/.claude/skills/coach*/`, leaves `~/.claude/coach/` data intact. After this, `/plugin install coach-claw` works cleanly.
2. **Auto-migrate via plugin SessionStart.** Plugin's first-run SessionStart detects v1.x leftovers, removes them in-place. Higher risk — runs on every session start until detection clears.
3. **Manual migration documented in MIGRATION.md.** Just docs. User runs the cleanup themselves. Highest user friction; lowest engineering cost.

**Decision needed**: pick a path; almost certainly path 1.

---

### Item 7 — Distribution & versioning strategy

**Problem**: Today distribution is npm. v2.0.0 distribution is Anthropic's plugin marketplace. Do we ship via both? Just plugin? Keep npm alive only for the OS-side companion?

**Research questions**:
- What does the plugin marketplace submission process look like? (Per docs: in-app forms at claude.ai/settings/plugins/submit and platform.claude.com/plugins/submit.) What's the review SLA? Are there ongoing maintenance obligations?
- Does the plugin marketplace support pre-release / beta channels? (Important for v2.0.0 rollout — we'd want a "v2.0.0-beta" track for early adopters before promoting to stable.)
- Can a plugin and an npm CLI share state cleanly? E.g., the npm CLI's `coach-claw migrate` writes a marker file that the plugin's SessionStart reads to know it's the migrated user.
- Does the plugin's `version` field in plugin.json need to match an npm package version, or can they evolve independently? Probably independent — they're different surfaces.

**Possible paths**:
1. **Plugin-only distribution for v2.0.0.** Drop npm entirely after migration window. The `coach-claw` CLI is gone. Migration command becomes a one-shot bash script delivered via `bash <(curl ...)`.
2. **Plugin + thin companion CLI.** Plugin ships in-Claude-Code; npm CLI ships only `setup`, `migrate`, `schedule`, `doctor` subcommands (no `install` — that's `/plugin install` now). Both maintained.
3. **Plugin in-Claude + companion script in repo.** Like (2) but the companion is a `bash <(curl ...)` script, not an npm package. Lower distribution surface but loses version pinning.

**Decision needed**: probably path 2 or 3.

---

## Out of scope (explicit non-goals)

- **Claude.ai web app integration.** Plugins listed in the marketplace are usable in Claude Code (terminal); Claude.ai web doesn't run hooks, statusLine, or local cron. v2.0.0 stays Claude-Code-only.
- **MCP server features.** Today's coach surface doesn't expose tools/resources via MCP — staying that way.
- **Automatic OS-level cron without user opt-in.** Even after conversion, scheduling daily cron remains an explicit user step. We do not silently install launchd plists from the plugin.
- **Renaming `coach-claw` to a shorter scope name.** Keeps the existing branding.
- **Schema breaking changes to `profile.yaml` beyond what migration requires.** Schema v1 → v2 only happens if we change the file format (Item 5). Otherwise schema_version stays at 1.

---

## Suggested next-session methodology

When resuming with a fresh session, the recommended order:

1. **Re-read this RFC** (`artifacts/v2-plugin-conversion-rfc.md`) end-to-end. Plus the plugin docs ([code.claude.com/docs/en/plugins](https://code.claude.com/docs/en/plugins) and `/en/plugins-reference`).
2. **Audit the current code paths** that the conversion touches:
   - `install.sh` (526 lines) — how much retires, how much moves to companion
   - `npm/coach-claw.js` (222 lines, post-Phase 2) — what stays vs what moves
   - `~/.claude/settings.json` patching at `install.sh:376-446` — replaced by plugin auto-discovery
   - `coach/bin/{configure,user_config,stats}.py` paths and env vars — already path-injectable from Phase 2, should mostly carry over
3. **Spawn parallel research agents** for the 7 items above. One agent per item is plausible; could batch related items (1+6 are UX/breaking-change concerns; 2+3+4 are integration concerns; 5 is a dep concern; 7 is distribution).
4. **Run a `/debate`** on the contentious decisions (especially Item 1 namespacing and Item 5 PyYAML replacement choice).
5. **Synthesize** + write a v2.0.0 implementation plan in `/Users/ryanmonroe/.claude/plans/`.
6. **Phase the work** — probably 5-7 phases:
   - Phase A: PyYAML replacement (or vendor)
   - Phase B: Plugin manifest + skill/hook restructure (no behavioral change yet)
   - Phase C: Companion CLI scope reduction
   - Phase D: Migration command
   - Phase E: statusLine workaround
   - Phase F: Beta release on marketplace
   - Phase G: Stable v2.0.0 + sunset announcement for v1.x

Each phase ships as its own commit; v2.0.0 doesn't release until all phases land.

---

## Open files / state at end of working session 2026-05-08

- `main` branch is **3 commits ahead of origin/main**:
  - `ab0610c` — Phase 1: post-install banner rewrite + `--no-seed` flag
  - `de5505e` — Phase 2: `coach-claw config <set|preview|wizard>` subcommand + `COACH_CONFIG_DIR` path injection
  - `32143d3` — Phase 3: collapse `/config preview` heredoc to call `configure.py` + bump to v1.0.5
- `package.json:3` is at `1.0.5`. **npm publish blocked until 3pm Eastern 2026-05-09** (npm 24h republish blackout from today's full unpublish at 3pm Eastern 2026-05-08).
- `CHANGELOG.md` has been updated locally with v1.0.3 / v1.0.4 / v1.0.5 entries, **but the file is in `.gitignore`** ("Local-only docs" per the comment) — those entries don't ship publicly.
- 479 tests pass (was 460 pre-Phase 1).
- All v1.x QA findings from teammate review are addressed in commit `32143d3`.

**Next session does NOT need to address v1.x release work**. Push the three local commits + run `npm publish` after 3pm Eastern 2026-05-09 — that's the v1.x close-out. v2.0.0 work starts from the `main` branch state at that point.

---

## Questions I still want answered before writing the v2.0.0 plan

1. **Does Anthropic's plugin marketplace tolerate plugins that ship a companion CLI?** I.e., is "plugin in marketplace + npm package on registry" an accepted pattern, or does Anthropic expect plugins to be entirely self-contained?
2. **What's the user-facing `/plugin` UX in Claude Code today?** Need to actually run `/plugin marketplace list`, `/plugin install <name>`, `/plugin uninstall <name>` and observe the experience to design the migration well.
3. **Is there a beta / pre-release channel for the marketplace?** Needed to roll v2.0.0 to early adopters without affecting v1.x users.
4. **Does plugin install/uninstall preserve `~/.claude/<random>/` state outside the plugin dir?** I.e., after `/plugin uninstall coach-claw`, does `~/.claude/coach/` survive? Almost certainly yes (it's outside the plugin dir) but worth confirming.
5. **How does PyYAML get installed for users who aren't familiar with `pip`?** This is the realest UX risk in Item 5. Today's install.sh handles it; v2.0.0 needs a clean answer.

---

*End of RFC. To pick this up: open a fresh session, read this file, then begin with item-by-item research per the methodology section.*
