#!/usr/bin/env bash
# Coach Claw plugin — skill-invocation wrapper.
#
# Same venv-or-system Python resolution as bootstrap.sh, but WITHOUT the
# coexistence guard. Skills (`/coach-claw:coach status` and friends) are
# user-invoked and explicit — they should always run, even when the npm
# CLI has hooks registered. The coexistence-defer protocol is for hooks
# only (which fire passively and would otherwise double-fire).
#
# bootstrap.sh now exec's into this script after its coexistence check,
# so the venv-setup logic lives in exactly one place.
#
# Invocation:
#   ${CLAUDE_PLUGIN_ROOT}/bin/run.sh path/to/script.py [args...]
#   ${CLAUDE_PLUGIN_ROOT}/bin/run.sh - [args...]    # script from stdin
#
# Example (in a SKILL.md heredoc):
#   ${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/configure.py preview

DATA_DIR="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/plugins/data/coach-claw}"
REQ="${CLAUDE_PLUGIN_ROOT}/requirements.txt"
VENV="$DATA_DIR/venv"
STAMP="$DATA_DIR/requirements.stamp"
PYBIN="$VENV/bin/python3"

mkdir -p "$DATA_DIR" 2>/dev/null

# Re-install when requirements.txt drift detected OR venv missing/broken.
if ! diff -q "$REQ" "$STAMP" >/dev/null 2>&1 || [ ! -x "$PYBIN" ]; then
  if python3 -m venv "$VENV" >/dev/null 2>&1; then
    if "$VENV/bin/pip" install -q -r "$REQ" >/dev/null 2>&1; then
      cp "$REQ" "$STAMP"
    fi
  fi
fi

# Prefer venv's python; fall back to system if setup failed.
if [ -x "$PYBIN" ]; then
  exec "$PYBIN" "$@"
else
  exec python3 "$@"
fi
