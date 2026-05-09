#!/bin/bash
# Coach Claw — wrap-mode statusline trampoline.
#
# Runs the user's saved original statusLine command first (captured in
# `~/.claude/coach/.statusline-wrap.json` by `statusline_wrap_action.wrap`),
# then appends the Coach segment with trailing-aware separator handling.
#
# Symmetric with `default-statusline-command.sh` — same `${BASH_SOURCE%/*}`
# parameter expansion to locate the bin/ dir relative to this wrapper, so
# custom CLAUDE_DIR installs keep working without configuration. `@PY@`
# is substituted with the absolute python3 path at install time.
#
# To opt out of wrap mode after install: `/coach-claw:doctor --unwrap-statusline`.

exec "@PY@" "${BASH_SOURCE%/*}/bin/statusline_wrap.py"
