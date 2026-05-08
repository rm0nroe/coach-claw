#!/bin/bash
# Coach Claw — default statusline composition.
#
# Thin trampoline: locates `bin/default_statusline.py` relative to this
# wrapper's own directory using bash parameter expansion only — no
# external commands, no PATH lookups. settings.json registers this
# wrapper by absolute path, so ${BASH_SOURCE%/*} reliably yields the
# wrapper's parent dir. Resolving relatively (instead of hardcoding
# $HOME/.claude/coach/...) keeps custom CLAUDE_DIR installs working.
#
# `@PY@` is substituted with the absolute python3 path at install time
# so the script doesn't depend on PATH at statusline-render time.
#
# To customize the rich default: edit `bin/default_statusline.py` (pure
# Python, no external dependencies). Or run `/config` to change the
# trailing coach segment's variant + theme without touching either
# file.

exec "@PY@" "${BASH_SOURCE%/*}/bin/default_statusline.py"
