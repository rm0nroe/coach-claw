# Contributing

Thanks for considering a contribution to Coach Claw. The project
is small and the bar for changes is high — better to land one focused
PR than three speculative ones.

## Quick start

```bash
git clone https://github.com/rm0nroe/coach-claw
cd coach-claw
python3 -m pip install --user pyyaml pytest
python3 -m pytest coach/tests/
```

If you want to run against your live coach install while iterating, run
`./install.sh` after each change — the bundle in this repo is the
source of truth and the installer pushes it to `~/.claude/`. Never edit
`~/.claude/hooks/*.py` or `~/.claude/coach/bin/*.py` directly.

## Test-first

Every change that touches scoring, merge, hooks, or marker I/O must come
with a test. The suite is fast — run it before opening a PR.

If you add a new behavior pattern, scoring action, or render shape, also
add a regression test. See `coach/tests/test_marker_writer_locking.py`
or `coach/tests/test_hook_render_env.py` for the shape.

## Code style

- Python 3.8+ compatible. No 3.9-only syntax (`Path.is_relative_to`,
  PEP 604 unions in non-`from __future__ import annotations` modules).
- 4-space indentation, snake_case, `from __future__ import annotations`
  where nearby modules already use it.
- Shared logic lives in shared modules. Extend `coach/bin/scoring.py`
  or `coach/bin/reward_hints.py` instead of duplicating heuristics in
  hooks or renderers. See `CLAUDE.md` "Key patterns" for the existing
  shared boundaries.
- Hook failures must never break Claude Code sessions. If you touch
  `hooks/*.py`, preserve the `try/except: _emit(None); sys.exit(0)`
  failsafe.
- No new abstractions until you have at least three call sites that
  would benefit. Three similar lines beats a premature helper.

## Commit + PR

- Imperative summaries: `Lock marker writers`, `Add Stripe redaction`.
- One concern per PR. Doc sweeps and code changes split into separate
  PRs unless the docs document the code change.
- Reference the BACKLOG.md ID if your change closes one
  (e.g. "closes the marker race P2").

## Reporting issues

Bug reports go through the GitHub issue tracker. Please include:

- Your OS + Python version (`python3 --version`)
- The output of `python3 -m pytest coach/tests/` if relevant
- Anything from `~/.claude/coach/log.ndjson` that looks related
  (it's redacted operational metadata, not transcript content)

For security-sensitive reports (a redaction gap, a path-traversal
concern), use the private reporting channels in `SECURITY.md` rather
than opening a public issue.

## Where to look

- `CLAUDE.md` — guidance for AI-assisted contributions; doubles as a
  good architecture overview.
- `AGENTS.md` — short project structure + build commands reference.
- `artifacts/architecture.md` — component deep-dives, data model.
- `BACKLOG.md` — open work, prioritized.
