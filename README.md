# Coach Claw

> [**rm0nroe.github.io/coach-claw →**](https://rm0nroe.github.io/coach-claw/) &nbsp;·&nbsp; live demo with the scroll-pinned tip walkthrough

A self-evolving coaching layer for [Claude Code](https://claude.com/claude-code). It learns
from your actual session transcripts and nudges your habits — both the ones to break and
the ones to keep going — with XP-rewarded ambient tips inside Claude Code.

A daily background pass (the deterministic Coach insights cron) detects concrete behavior
patterns from your transcripts. The `SessionStart` hook injects the watch-list into Claude's
context. The `UserPromptSubmit` hook runs a deterministic scheduler — picks a tip, pre-computes
its reward attribution, asks Claude to render it. Follow the tip → next turn shows `✅ Tip cleared`.
Five clean Coach insights runs in a row → the weakness graduates and retires. Lifetime XP
crosses a threshold → 🎉 Level up.

**Cron path: local-only, zero network.** Transcripts and the profile live under `~/.claude/`.
The scheduled cron pass is pure Python — no LLM call, no token cost. **Weekly path + on-demand
`/coach-insights`** triggers Claude Code's built-in `/insights` once per 7 days (via `claude -p`)
to refresh structured `facets/*.json` sidecar data. Coach itself does not independently upload
transcripts; the nested `/insights` refresh is an Anthropic-side Claude Code operation that
runs inside the user's existing authenticated session and writes only to local sidecar files.
Coach reads those local sidecars. The LLM call's content output is discarded — only the sidecar
refresh matters. Detections come from deterministic Python aggregation of stable enum keys.

---

## Install

Coach Claw ships two parallel distributions. Pick whichever you prefer
— they share the same `~/.claude/coach/` state, so you can use either
or both:

| Path | Best for | Command |
|---|---|---|
| **npm CLI** (canonical) | Provider-agnostic + OS-side cron + statusLine | `npx @rm0nroe/coach-claw@latest install` |
| **Claude Code plugin** | Marketplace install, no terminal needed | `/plugin marketplace add rm0nroe/coach-claw-plugin-marketplace` then `/plugin install coach-claw@coach-claw-plugins` |

The npm CLI is the canonical path — it manages launchd/cron registration,
patches the main statusline, and works regardless of which AI coding
assistant you're using day-to-day. The plugin is a Claude-Code-native
alternative that self-installs hooks and statusline; it nudges you to
the npm CLI for OS-level scheduling that the plugin model can't
register on its own. When both are installed, the plugin defers to the
CLI by default (run `/coach-claw:switch` to flip control).

### Prerequisites (both paths)

- macOS 11+ or Linux
- `bash`, `git`, Python 3.8+ (system or Homebrew)
- [Claude Code](https://claude.com/claude-code) installed and run at least once
- PyYAML — the npm installer auto-installs it; the plugin path
  provisions a per-plugin venv on first SessionStart. See
  [Troubleshooting](#troubleshooting) if either path's PyYAML setup fails.

### Install via npm CLI

1. **Check your machine**

   ```bash
   npx @rm0nroe/coach-claw@latest doctor
   ```

2. **Run the installer**

   ```bash
   npx @rm0nroe/coach-claw@latest install --seed
   ```

   `--seed` is optional but recommended — it pre-populates your profile
   from the last 7 days of transcripts so your first session isn't empty.
   See [Install options](#install-options) for the full flag list.

3. **Register the daily insights cron**

   - **macOS**:
     ```bash
     npx @rm0nroe/coach-claw@latest launchd
     ```
   - **Linux** — `crontab -e`, then add (runs daily at 04:00 local):
     ```
     0 4 * * * $HOME/.claude/coach/bin/insights.sh 1d >> /tmp/claude-coach.log 2>&1
     ```

4. **Verify**

   ```bash
   npx @rm0nroe/coach-claw@latest doctor
   ```

   Open a fresh Claude Code session and run:

   ```
   /coach status
   ```

   You should see your level, ELO, and (if `--seed` was passed) a
   tracked weakness or two.

5. **(Optional) Customize the look**

   ```bash
   npx @rm0nroe/coach-claw@latest config wizard
   ```

   Interactive picker for statusline variant + theme. Or
   non-interactive:

   ```bash
   npx @rm0nroe/coach-claw@latest config set --theme ocean --statusline pips
   ```

   Same backing file as the `/config` slash command inside Claude
   Code — `~/.claude/coach/.user_config.json`. Run either surface
   any time, they don't conflict.

### Install options

| Flag | Effect |
|---|---|
| `--seed` (alias `--bootstrap`) | After install, run a 7-day insights pass against your existing transcripts so the profile isn't empty on day one. |
| `--no-seed` | Skip seeding explicitly (forward-compatible with a future TTY-gated seed prompt). Mutually exclusive with `--seed`. |
| `--fresh` | Skip recovery from a prior `/coach uninstall`. Forces a true fresh install (drops any `coach.bak.<ts>/` siblings). |
| `--no-prune-backups` | Keep ALL `coach.bak.<ts>/`, `settings.json.bak.<ts>`, and `hooks/*.bak.<ts>`. Default trims to the 3 most recent of each kind. |
| `-h`, `--help` | Print full usage and exit. |

### Install via Claude Code plugin

If you'd rather install from inside Claude Code, add the marketplace
once and then install the plugin:

```
/plugin marketplace add rm0nroe/coach-claw-plugin-marketplace
/plugin install coach-claw@coach-claw-plugins
```

After install, restart Claude Code (or open a new session) so the
plugin's SessionStart hook can:

- bootstrap a per-plugin Python venv at `${CLAUDE_PLUGIN_DATA}/venv/`
  (PyYAML lands there automatically)
- self-install the main statusline into `~/.claude/settings.json`

The plugin namespaces all skills under `/coach-claw:`:

| npm CLI command | Plugin equivalent |
|---|---|
| `/coach status` | `/coach-claw:coach status` |
| `/coach-insights` | `/coach-claw:coach-insights` |
| `/config` | `/coach-claw:config` |
| _(none — npm-only)_ | `/coach-claw:switch` (flip control to plugin if both installed) |
| _(none — npm-only)_ | `/coach-claw:doctor` (diagnose plugin state; `--remove-statusline`, `--wrap-statusline`, `--unwrap-statusline` actions) |

When Claude Code starts a session and finds a custom statusline, the
plugin auto-wraps it: your existing command keeps rendering, and the
Coach segment appends to it. Original is preserved in
`~/.claude/coach/.statusline-wrap.json`. Run
`/coach-claw:doctor --unwrap-statusline` to revert (sticky — the
plugin won't auto-rewrap). The CLI installer (`npx coach-claw install`)
does the same wrap on a fresh box.

The plugin **does not** register the daily insights cron. The first
time you start a session under a plugin-only install, you'll see a
one-time banner suggesting `npx @rm0nroe/coach-claw@latest launchd`.
The cron stays an npm-CLI feature because the plugin model has no
equivalent for OS-level scheduling.

### Troubleshooting

**`ERROR: could not install PyYAML automatically` on macOS**

Homebrew Python 3.12+ blocks `pip install --user` per PEP 668. The
installer auto-retries with `pip … --break-system-packages` (safe for
a per-user library install — the marker exists to protect *system*
site-packages, which `--user` doesn't touch). If both attempts fail,
install PyYAML manually with one of:

```bash
python3 -m pip install --user --break-system-packages pyyaml
# or, if you'd rather isolate it:
python3 -m venv ~/.coach-venv && ~/.coach-venv/bin/pip install pyyaml
# then re-run install.sh with that venv's python3 on PATH:
PATH=~/.coach-venv/bin:$PATH ./install.sh
```

Then re-run `./install.sh` (the first form) or use the `PATH=…` prefix
shown above (the venv form).

**`python3 not found in PATH`**

Install Python 3.8+ via your OS package manager (`brew install python`
on macOS) and re-run.

**More**: see
[`artifacts/infrastructure.md`](./artifacts/infrastructure.md).

---

## Use

Inside Claude Code (slash commands):

```
/coach status        level, XP, weakness streaks, last Coach insights run
/coach off | on      toggle without uninstalling
/coach uninstall     mv (not rm) to ~/.claude/coach.bak.<ts>
/coach-insights      manual LLM-driven pass — refreshes /insights facets
                       sidecars, aggregates stable enum keys, merges
                       into profile by default. Pass --dry-run to print
                       the detections JSON without merging. (Also fires
                       automatically once / 7d on session start.)
/config              statusline variant + theme + ELO range
                       (4 variants × 12 themes — see /config preview)
```

From the terminal (same backing file as `/config`):

```
npx @rm0nroe/coach-claw@latest config preview                 # see all combos
npx @rm0nroe/coach-claw@latest config wizard                  # interactive picker
npx @rm0nroe/coach-claw@latest config set --theme ocean       # scriptable
npx @rm0nroe/coach-claw@latest config set --statusline pips
npx @rm0nroe/coach-claw@latest config set --elo 1200 2800
```

Per-session kill: `COACH_DISABLE=1 claude`.

---

## Cost

| | Input tokens / session | Notes |
|---|---:|---|
| `SessionStart` block | ~2,400 | once per session, prompt-cacheable |
| Tip when fired | ~530 | 1–3 / session, probability + cooldown gated |
| Scheduled cron insights pass | **0** | pure Python, runs daily at 04:00 |
| Weekly LLM-driven trigger | one Claude session / 7 days | spawns `claude -p "/insights"` for the side effect of refreshing sidecars; output discarded |
| On-demand `/coach-insights` | one Claude session per `--force` invocation | same wrapper as the weekly trigger |
| Statusline + `/coach status` | **0** | pure Python |

≈ $0.03–$0.10/day on Sonnet, $0.15–$0.50/day on Opus at 5 sessions/day.
Weekly LLM trigger adds ~$0.01–$0.03/week (~$0.50/year) — we don't pay
for output tokens we don't read.

---

## Privacy

**Cron path (the deterministic daily insights pass):** `coach/bin/redact.py` runs before any
transcript byte reaches `analyze.py`. Strips API keys (Anthropic, OpenAI, AWS, GitHub, Slack,
Stripe, HuggingFace, npm, Google), Bearer tokens, PEM blocks, JWTs, and long hex secrets.
Over-redacts by design — false positives are cheap, leaked credentials are not. `profile.yaml`
ends up with only pattern slugs, counts, and timestamps. Nothing leaves your machine.

**Weekly path + on-demand `/coach-insights`:** triggers Claude Code's built-in `/insights`
once per 7 days (via a `claude -p` subprocess). That's an Anthropic-side LLM step you're already
inside of by virtue of running Claude Code — Coach just runs it for the side effect of refreshing
`~/.claude/usage-data/facets/*.json`. We discard the CLI's content output and read only the
locally-written sidecar JSON Anthropic produces. Detections derive from stable enum keys
(`friction_counts.*`, `primary_success`) — deterministic Python aggregation, no slug translation,
no fuzzy matching. `profile.yaml` still ends up with only short summaries (≤120 chars, file
paths and extensions redacted); raw transcript content never lands in Coach state.

---

## Tunable

For display (statusline variant, level-name theme, ELO range): use
`/config`. Settings persist to `~/.claude/coach/.user_config.json`.
Tip behavior + math are still constants at the top of two files:

- `~/.claude/hooks/coach-user-prompt.py` — `TIP_FIRE_PROBABILITY`, cooldowns, label rotation
- `~/.claude/coach/bin/merge.py` — confidence math, debounce, decay, graduation thresholds

---

## Failsafe

Both hooks wrap `main()` in `try/except` and always `exit 0`. A coach bug cannot break a
Claude Code session.

---

## Tests

```bash
cd ~/.claude/coach && python3 -m pytest tests/
```

---

## Docs

- [`artifacts/architecture.md`](./artifacts/architecture.md) — full loop, components,
  data model, scheduler internals, design rationale
- [`artifacts/infrastructure.md`](./artifacts/infrastructure.md) — install, runbook,
  Linux cron, launchd, troubleshooting, config reference
- [`BACKLOG.md`](./BACKLOG.md) — open work
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — test-first workflow, code style, PR expectations
- [`CLAUDE.md`](./CLAUDE.md) — guidance for Claude Code sessions on this codebase
- [`AGENTS.md`](./AGENTS.md) — project structure + build commands

---

## License

MIT — see [`LICENSE`](./LICENSE).

## Acknowledgments

The lobster mark in `artifacts/social-preview.png` is adapted from
[Twemoji](https://github.com/jdecked/twemoji) (codepoint U+1F99E), CC-BY 4.0.
