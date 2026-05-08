# Repository Guidelines

## Project Structure & Module Organization

This repository is a shareable Coach Claw bundle. Source lives in `coach/bin/` and installs to `~/.claude/coach/bin/`. Claude hook entrypoints live in `hooks/`, slash-command skills in `skills/coach/` and `skills/coach-insights/`, and macOS scheduler assets in `launchd/`. Tests are in `coach/tests/`. Architecture and infrastructure notes are in `artifacts/`; setup details are in `README.md` and operator notes in `coach/README.md`.

## Build, Test, and Development Commands

There is no compile step; this is Python 3.8+ and shell.

```bash
./install.sh                  # install/update into ~/.claude/, preserving profile data
./install.sh --seed           # install and seed profile from recent transcripts
./install-launchd.sh          # macOS: register the daily Coach insights pass and run once
python3 -m pip install --user pyyaml pytest
python3 -m pytest coach/tests # run tests from the repo checkout
```

After installation, smoke-test the live hooks with:

```bash
echo '{}' | python3 ~/.claude/hooks/coach-session-start.py
echo '{}' | python3 ~/.claude/hooks/coach-user-prompt.py
```

## Coding Style & Naming Conventions

Use standard Python with 4-space indentation, `from __future__ import annotations` where it matches nearby modules, and clear snake_case names for modules, functions, and variables. Keep shell scripts POSIX-conscious unless they already require Bash. Shell scripts should invoke `python3` from `PATH`; Python subprocesses should prefer `sys.executable`. Do not hardcode `/usr/bin/python3`.

Keep shared behavior in shared modules: extend `coach/bin/scoring.py` or `coach/bin/reward_hints.py` instead of duplicating detection heuristics in hooks or renderers. Hook failures must never break Claude Code sessions; preserve broad failsafes and exit-0 behavior.

## Testing Guidelines

The pytest suite covers scoring, merge behavior, reward hints, stats, bank concurrency, skill inventory, and hook relevance. Add or update focused tests in `coach/tests/test_*.py` when changing those paths. Run `python3 -m pytest coach/tests` before handoff. For installed-state regressions, also run tests from `~/.claude/coach` after `./install.sh`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Fix review-flagged bugs in project-scoped skill filtering` and `Document manual insights.sh re-run double-count`. Keep commits narrowly scoped and mention the user-visible behavior changed.

Pull requests should include a brief problem/solution summary, test results, and any install or migration notes. Include screenshots only for visible output changes; otherwise paste representative command output or hook smoke-test results.

## Security & Configuration Tips

This tool reads local Claude transcripts, so avoid logging raw secrets. Keep redaction before analysis (`coach/bin/redact.py`) and preserve local-only behavior. Runtime data such as `profile.yaml`, `changelog.md`, and banked session state is preserved by the installer; do not overwrite it casually.
