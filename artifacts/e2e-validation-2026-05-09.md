# Coach Claw plugin — e2e validation 2026-05-09

End-to-end walkthrough of the plugin distribution against a real
`claude` CLI runtime. Plan: `~/.claude/plans/read-artifacts-v2-plugin-conversion-rfc-atomic-pudding.md`.

**Outcome**: 1 real bug found and fixed (commit `a54c971`, plugin
v0.1.0 → v0.1.1). All other phases passed.

---

## Pre-test environment

- Plugin v0.1.0 installed via public marketplace
  (`/plugin install coach-claw@coach-claw-plugins`)
- npm CLI hooks registered in `~/.claude/settings.json`
- npm CLI install dir at `~/.claude/coach/bin/` predates the new
  plugin-track modules (`cron_check`, `statusline_self_patch`,
  `coexistence_check`, `coach_paths`) — none of those exist there
- Custom non-Coach statusLine in settings.json
  (`bash ~/.claude/statusline-command.sh` — user-installed)
- launchd plist `com.local.claude-coach` loaded

---

## Phase A — slash commands (no neutering, plugin deferred)

All five run via `COACH_DISABLE=1 claude -p "<command>"` so the CLI
hooks silence themselves (preventing test side effects) and the
plugin's hooks defer. Slash commands resolve via Claude Code's skill
loader, which is independent of hooks.

| # | Command | Result |
|---|---|---|
| A1 | `/coach-claw:coach status` | **PASS** — full status block: L8 Sensei, 97 xp, lifetime/session breakdowns, weakness watch-list, last-insights timestamp |
| A2 | `/coach-claw:config show` | **PASS** — variant=crystal, theme=craft, ELO=1000-2800 |
| A3 | `/coach-claw:config preview` | **PASS** (verified by direct execution of `configure.py preview` — `claude -p` summarized the output) |
| A4 | `/coach-claw:coach-insights --dry-run` | **PASS** — full detection JSON output (3 detections at ~0.3 ratio over 95 sessions) |
| A5 | `/coach-claw:switch --dry-run` | **PASS** — "Would remove 2 CLI hook entries" |

No `ModuleNotFoundError`. No `${CLAUDE_PLUGIN_ROOT}` expansion issues.
System Python had PyYAML available (npm CLI had installed it
previously), so the slash-command-bypasses-bootstrap path didn't
surface a venv gap here.

---

## Phase B — hook-firing (after explicit switch)

### B0 — pre-switch snapshot
Captured for restoration verification. CLI hooks present (2),
custom statusLine present, launchd loaded.

### B1 — `/coach-claw:switch`
**PASS**: removed 2 CLI hook entries; CLI statusLine NOT removed
(it's not the canonical CLI's `default-statusline-command.sh`, so
`switch_to_plugin.py` correctly didn't touch it). Wrote
`.cli-uninstalled-by-plugin` marker; cleared `.plugin-deferred`.

### B2 — `launchctl bootout` of Coach plist
**PASS** on second attempt (used `bootout gui/$UID` rather than
`unload`; the older `unload` had partial effect and launchd was
auto-reloaded somehow — `bootout` was decisive).

### B3 — fresh session via `claude -p` — **REAL BUG SURFACED HERE**

**First attempt** (with v0.1.0 plugin):
- venv was provisioned at `~/.claude/plugins/data/coach-claw-coach-claw-plugins/venv/` ✓
- statusLine NOT changed (expected — claimed branch)
- **`.cron-nudged` marker NOT written** ✗ — UNEXPECTED

**Diagnosis**: the plugin's hooks
(`hooks/coach-{session-start,user-prompt}.py`) put
`${COACH_DIR}/bin/` (= `~/.claude/coach/bin/`, the npm CLI's install
dir) on `sys.path`, NOT `${CLAUDE_PLUGIN_ROOT}/bin/`. The npm CLI's
install on this machine pre-dates the new plugin-track modules, so
the plugin's `from cron_check import is_cron_registered` silently
failed under the failsafe try/except. Result: hook fired, but the
new plugin behaviors (statusLine self-install, cron-nudge) silently
no-op'd. No error. No log line.

**Fix** (commit `a54c971`, plugin v0.1.0 → v0.1.1):

```python
# hooks/coach-{session-start,user-prompt}.py
_PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
if _PLUGIN_ROOT:
    sys.path.insert(0, str(Path(_PLUGIN_ROOT) / "bin"))
else:
    sys.path.insert(0, str(COACH_DIR / "bin"))
```

Pinned by `coach/tests/test_hook_module_resolution.py` (4 tests,
2x2 matrix of {hook} × {context}).

**Re-run after fix** (with v0.1.1 plugin):
- B5 — venv: **PASS** — `~/.claude/plugins/data/coach-claw-coach-claw-plugins/venv/bin/python3 -c "import yaml" → 6.0.3`
- B6 — `.cron-nudged` marker: **PASS** — written with timestamp `2026-05-09T03:20:18.280319+00:00`
- B4 — statusLine claimed branch: **PASS** — plugin saw user's existing non-Coach statusLine, classified as "claimed", left it untouched

### B4b — `installed` branch (cleared statusLine first)
**PASS** — with statusLine entry removed from settings.json,
plugin's next session start self-patched it to:

```
/Users/ryanmonroe/.claude/plugins/cache/coach-claw-plugins/coach-claw/0.1.1/bin/bootstrap.sh /Users/ryanmonroe/.claude/plugins/cache/coach-claw-plugins/coach-claw/0.1.1/bin/default_statusline.py
```

Absolute paths to the v0.1.1 cache (no `${CLAUDE_PLUGIN_ROOT}`
literal — confirmed Claude Code does NOT expand env vars in keys
plugins write into settings.json, exactly as my plan predicted).
User's original statusLine restored after the test.

---

## Phase C — restore baseline

- C1 — `launchctl bootstrap` to re-load Coach plist → loaded
- C2 — `npx @rm0nroe/coach-claw@latest install` → **FAILED 404**:
  npm package isn't published yet (v1.0.5 was unpublished prior to
  this session's start; the user hasn't done `npm publish` yet).
- C2-fallback: manually re-added CLI hook entries to settings.json
  via Python, matching the install.sh shape.
- C3 verification:
  - 2 CLI hook entries present in settings.json ✓
  - Plugin bootstrap defers (rc=0, no python execution) ✓
  - `.plugin-deferred` marker rewritten ✓
  - User's custom statusLine still in place ✓

---

## Discovered gaps (filed for follow-up)

1. **Slash commands bypass bootstrap.sh's venv setup** — **FIXED in
   v0.1.2** (commit `2766eb7`). New `plugin/bin/run.sh` is the
   skill-invocation wrapper: same venv-or-system Python resolution
   as bootstrap.sh, but without the coexistence guard. Bootstrap now
   delegates to it (`exec ${CLAUDE_PLUGIN_ROOT}/bin/run.sh "$@"`)
   after its coexistence check, so the venv-setup logic lives in
   exactly one place. All plugin SKILL.md files updated to invoke
   `${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/X.py`
   (and `run.sh -` for heredocs). insights-llm.sh + insights.sh
   prepend `$CLAUDE_PLUGIN_DATA/venv/bin` to PATH at startup so
   their internal `python3` calls also resolve to the venv.
   13 new tests pinning the contracts (5 run.sh, 4 skills-use-run.sh,
   4 insights-llm-venv-path). Live verification post-update:
   `run.sh -c "import sys,yaml; print(sys.executable)"` returns the
   venv python, not system.

2. **`launchctl unload` not fully decisive** — first unload of
   Coach plist appeared to succeed but the service reported as
   loaded again moments later. `launchctl bootout gui/$UID
   <plist>` (newer command) was decisive. No code change needed;
   note for future testing scripts.

3. **npm v1.0.5 still unpublished** — the npx install fallback in
   Phase C wouldn't have worked even with the test running fresh.
   Standalone follow-up (see `~/.claude/plans/...` previous
   session's checkpoint).

---

## Final state

Baseline restored. Plugin installed at v0.1.1 deferred to npm CLI;
CLI hooks active; user's custom statusLine intact; launchd Coach
plist loaded; `~/.claude/coach/` git-tracked state untouched.

Test commits this session:
- `a54c971` v0.1.1 hook sys.path fix + 4 regression tests
- (pending — this writeup commit)

Total tests: **564 passing** (up from 560 pre-e2e). Zero skips.
