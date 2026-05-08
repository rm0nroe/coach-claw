---
description: "Customize the Coach Claw display — statusline variant, rank-name theme, ELO range. Usage: /config [show|preview|statusline <variant>|theme <name>|elo <min> <max>|reset]"
---

The Coach reads `~/.claude/coach/.user_config.json` at every render. This
skill is the slash-command surface for editing it without touching files
by hand. Three things are tunable:

- **Statusline variant** — how the trailing coach segment renders. Five
  options: `crystal`, `pips`, `bracket`, `slash`, `forge`.
- **Theme** — the 50-name level ladder. Twelve options: `craft`
  (default), `forge`, `cosmic`, `ocean`, `skyrim`, `marvel`, `dc`,
  `finalfantasy`, `military`, `lotr`, `starwars`, `hacker`.
- **ELO range** — `elo_min` and `elo_max` (defaults 1000 → 2800). The
  rating is linearly interpolated across the 50-level ladder.

The threshold curve (XP per level) is not configurable — keeping it
fixed means existing XP totals never trigger retroactive level-ups.

## Argument

The argument after `/config` is one of:
`show` | `preview` | `statusline <variant>` | `theme <name>` |
`elo <min> <max>` | `reset`. If no argument is provided, default to `show`.

## Behavior per argument

### `show` (default)

Read the current config and print a tidy summary. No mutations.

```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/coach/bin"))
from user_config import load
from statusline_variants import list_variants
from themes import list_themes
cfg = load()
print(f"  Variant : {cfg['statusline_variant']}  (options: {', '.join(list_variants())})")
print(f"  Theme   : {cfg['theme']}  (options: {', '.join(list_themes())})")
print(f"  ELO     : {cfg['elo_min']} → {cfg['elo_max']}")
PY
```

### `preview`

Render every variant × the user's current theme, plus every theme name
at L1 / L25 / L50, so the user can see what the options look like
side-by-side before committing. Pure read — no mutations.

Delegates to `coach/bin/configure.py preview` so the slash command and
the `npx coach-claw config preview` terminal command run literally the
same code — output is byte-identical, no drift surface.

```bash
python3 ~/.claude/coach/bin/configure.py preview
```

### `statusline <variant>`

Set `statusline_variant`. Validate against the registered set; on a
typo, print the valid options and exit without mutating.

```bash
VARIANT="$1"  # the second word from the user's /config invocation
python3 - "$VARIANT" <<'PY'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/coach/bin"))
from user_config import update, VALID_VARIANTS
v = sys.argv[1] if len(sys.argv) > 1 else ""
if v not in VALID_VARIANTS:
    print(f"unknown variant {v!r}. valid: {sorted(VALID_VARIANTS)}")
    sys.exit(1)
update(statusline_variant=v)
print(f"statusline → {v}")
PY
```

Confirm with one line: `Statusline updated to <variant>. Open a new prompt to see it.`

### `theme <name>`

Set `theme` analogously. Same shape as `statusline` — validate, update,
confirm.

```bash
THEME="$1"
python3 - "$THEME" <<'PY'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/coach/bin"))
from user_config import update, VALID_THEMES
t = sys.argv[1] if len(sys.argv) > 1 else ""
if t not in VALID_THEMES:
    print(f"unknown theme {t!r}. valid: {sorted(VALID_THEMES)}")
    sys.exit(1)
update(theme=t)
print(f"theme → {t}")
PY
```

### `elo <min> <max>`

Set the ELO interpolation range. Validate `0 < min < max`. Same
update + confirm shape.

```bash
MIN="$1"; MAX="$2"
python3 - "$MIN" "$MAX" <<'PY'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/coach/bin"))
from user_config import update
try:
    emin = int(sys.argv[1]); emax = int(sys.argv[2])
except (IndexError, ValueError):
    print("usage: /config elo <min> <max>"); sys.exit(1)
if not (0 < emin < emax):
    print(f"elo_min ({emin}) must be a positive int less than elo_max ({emax})")
    sys.exit(1)
update(elo_min=emin, elo_max=emax)
print(f"elo → {emin} → {emax}")
PY
```

### `reset`

Delete `~/.claude/coach/.user_config.json` so all settings revert to
their defaults (`crystal` + `craft` + `1000–2800`). Ask for explicit
confirmation first.

```bash
rm -f "$HOME/.claude/coach/.user_config.json"
echo "Config reset. Defaults: crystal + craft + 1000-2800."
```

## Rules

- Never edit `profile.yaml`, `banked_sessions.json`, or any `.pending_*`
  marker via this skill — those are autonomous-loop state, not user
  config.
- Never invent new variant or theme names. The validators in
  `user_config.py` reject unknown values; show the user the registered
  options instead.
- For `elo`: the threshold curve is hardcoded; only the ELO
  interpolation range is user-tunable. If asked to change XP-per-level
  thresholds, point at `coach/bin/stats.py:_build_level_ladder` and
  warn that doing so retroactively shifts existing user levels.
- After any mutation, remind the user: "Open a new prompt or restart
  Claude Code to see the new statusline render."
