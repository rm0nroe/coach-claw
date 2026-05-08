#!/bin/bash
# Wrapper invoked by launchd to run the Coach insights pass once. Logs to
# /tmp. Runs the deterministic insights.sh — does NOT go through the
# claude CLI, so no cold-start cost and no slash-command routing issues.
# (The on-demand `/coach-insights` skill is the LLM-driven counterpart
# that runs from inside Claude Code; this wrapper is launchd-only.)

set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="${HOME:-$(eval echo ~$(whoami))}"

LOG="/tmp/claude-coach.log"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[$TS] starting insights.sh 1d" >> "$LOG"

"$HOME/.claude/coach/bin/insights.sh" 1d >> "$LOG" 2>&1
EXIT=$?

echo "[$TS] insights.sh exited $EXIT" >> "$LOG"
exit "$EXIT"
