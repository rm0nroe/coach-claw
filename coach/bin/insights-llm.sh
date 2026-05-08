#!/bin/bash
# Coach Claw — LLM-triggered weekly insights pass.
#
# Pipeline:
#   1. Generate a unique RUN_ID with the `insights-weekly-` prefix so
#      downstream consumers (changelog, recent_runs) can distinguish from
#      the daily deterministic path's `insights-<ts>` runs.
#   2. Invoke `claude -p "/insights"` with COACH_DISABLE=1 to refresh
#      Anthropic-side facets/*.json sidecars. The CLI's stdout is
#      DISCARDED — we run it for the side effect on disk only.
#   3. Aggregate the refreshed facets via aggregate_facets.py (pure
#      Python, deterministic) → detections JSON.
#   4. Hand to merge.py. Same merge logic as the daily path. No schema
#      change. Run-id prefix is the only discriminator.
#   5. Auto-commit + touch .last_weekly_insights to throttle the
#      SessionStart trigger to ≥7 days.
#
# Flags:
#   --dry-run    aggregate + print detections JSON; do not call merge.py
#                or touch the throttle marker.
#   --force      run even if .last_weekly_insights mtime is < 7 days ago.
#                Without this flag the script exits 0 silently when the
#                throttle is fresh.
#
# Exit codes:
#   0   success (full run completed, or skipped on stale-marker throttle,
#       or skipped on --dry-run after printing detections)
#   2   python3 not on PATH (or unknown CLI arg)
#   4   merge.py failed
#   5   aggregate_facets.py failed (nonzero exit OR unparseable JSON);
#       wrapper bails BEFORE merge + marker touch so the next session
#       can retry from scratch
#   6   LLM refresh step failed (claude missing / nonzero exit / timeout);
#       wrapper bails BEFORE merge + marker touch. Same reasoning as
#       exit 5 — without a successful refresh, aggregating stale or
#       empty facets and merging the result would advance absence-based
#       streaks on phantom evidence.
#   7   no current-window evidence (n_sessions == 0 in window); wrapper
#       bails BEFORE merge + marker touch. Aggregator emits exit 3
#       (EXIT_NO_EVIDENCE) for this case; wrapper translates to 7. An
#       empty detections list with zero sessions in window is "no
#       evidence," which must NOT advance absence-based streaks. (Empty
#       detections WITH n_sessions > 0 IS valid — a clean week — and
#       merges normally.)
#   10  concurrent run already in progress (lock held by another
#       insights-llm.sh on the same .weekly_insights.lock); emitted by
#       run_with_lock.py during the at-startup re-exec. The losing
#       wrapper exits cleanly without invoking aggregator/merge.
#
# Cross-platform notes:
#   - Resolves python3 from PATH (no /usr/bin/python3 hardcode).
#   - mktemp templates use trailing Xs (BSD-safe, see CLAUDE.md gotcha).
#   - Date math is delegated to Python — no BSD-vs-GNU `date` flag drift.

set -uo pipefail

# Plugin-context PATH wedge: when invoked from inside a Claude Code
# plugin install (CLAUDE_PLUGIN_DATA env var set + venv exists),
# prepend the plugin's venv bin/ to PATH so subsequent `python3`
# resolutions in this script (and child python processes) pick up
# the venv's interpreter — and therefore PyYAML. CLI users never
# have CLAUDE_PLUGIN_DATA set; this is a no-op for them. See
# artifacts/e2e-validation-2026-05-09.md for context.
if [[ -n "${CLAUDE_PLUGIN_DATA:-}" && -x "$CLAUDE_PLUGIN_DATA/venv/bin/python3" ]]; then
  export PATH="$CLAUDE_PLUGIN_DATA/venv/bin:$PATH"
fi

# Resolve env-derived paths + python BEFORE arg parsing so the
# concurrent-run guard can re-exec us with the original argv.
COACH_DIR="${COACH_DIR_OVERRIDE:-$HOME/.claude/coach}"
THROTTLE_MARKER="$COACH_DIR/.last_weekly_insights"
FACETS_DIR="${COACH_FACETS_DIR:-$HOME/.claude/usage-data/facets}"

# Resolve sibling scripts via the shipping bash file's own location, not via
# COACH_DIR. Lets the test suite point COACH_DIR_OVERRIDE at a throwaway
# tmpdir while still loading aggregate_facets.py/merge.py from the installed
# (or source-tree) bin/ next to this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "python3 not found in PATH" >&2
  exit 2
fi

# --- Concurrent-run guard --------------------------------------------------
# Two SessionStart hooks firing within the slow `claude -p "/insights"`
# window will both see `.last_weekly_insights` as stale and try to spawn
# this wrapper. Re-exec ourselves through `run_with_lock.py` so the
# second invocation either sees the post-refresh fresh marker (under
# the lock, after the first finishes) and skips on throttle, or hits
# the lock itself and skips on contention. Either way, exactly one
# wrapper does the LLM call + merge.
#
# Must precede arg parsing — otherwise `"$@"` is empty after the parse
# loop and the re-execed copy can't see --dry-run / --force.
#
# COACH_LLM_LOCK_HELD=1 is the re-entry sentinel — set by
# run_with_lock.py on the wrapped process so we don't loop.
if [[ -z "${COACH_LLM_LOCK_HELD:-}" ]]; then
  mkdir -p "$COACH_DIR" 2>/dev/null
  exec "$PY" "$SCRIPT_DIR/run_with_lock.py" \
    "$COACH_DIR/.weekly_insights.lock" \
    bash "${BASH_SOURCE[0]}" "$@"
fi

# --- Below this line we hold the weekly-insights lock ---------------------

DRY_RUN=0
FORCE=0
TIMEOUT_SECS="${COACH_INSIGHTS_LLM_TIMEOUT:-300}"
THROTTLE_DAYS="${COACH_INSIGHTS_LLM_THROTTLE_DAYS:-7}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force)   FORCE=1;   shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# RUN_ID generation is inside the lock so two retried invocations of
# the same SessionStart wave can't stamp identical timestamps if the
# system clock resolves at one-second granularity.
RUN_ID="insights-weekly-$(date -u +%Y%m%dT%H%M%SZ)"

# --- Throttle check (skipped on --force / --dry-run) -----------------------
if [[ "$FORCE" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
  if [[ -f "$THROTTLE_MARKER" ]]; then
    AGE_SECS="$("$PY" - "$THROTTLE_MARKER" <<'PY'
import os, sys, time
try:
    mtime = os.path.getmtime(sys.argv[1])
    print(int(time.time() - mtime))
except Exception:
    print(-1)
PY
)"
    THROTTLE_SECS=$(( THROTTLE_DAYS * 86400 ))
    if [[ "$AGE_SECS" -ge 0 && "$AGE_SECS" -lt "$THROTTLE_SECS" ]]; then
      echo "skipped (throttle: last run ${AGE_SECS}s ago, threshold ${THROTTLE_SECS}s)"
      exit 0
    fi
  fi
fi

echo "run_id=$RUN_ID dry_run=$DRY_RUN force=$FORCE timeout=${TIMEOUT_SECS}s"

# --- Step 1: refresh facets via /insights subprocess -----------------------
# We discard stdout/stderr — only the side effect on facets/*.json matters.
# COACH_DISABLE=1 prevents the nested Claude session from loading Coach's
# own hooks (which would contaminate the analysis).
#
# COACH_INSIGHTS_LLM_SKIP_REFRESH=1 lets tests bypass the subprocess and
# operate on a pre-seeded fixture facets dir.
if [[ -z "${COACH_INSIGHTS_LLM_SKIP_REFRESH:-}" ]]; then
  CLAUDE_BIN="$(command -v claude || true)"
  if [[ -z "$CLAUDE_BIN" ]]; then
    # Fail-hard: aggregating stale-or-empty facets and merging the result
    # would touch .last_weekly_insights and advance absence-based streaks
    # on phantom evidence. Mirror the aggregator-fail-hard treatment
    # below — bail before merge so the next session retries cleanly.
    echo "claude CLI not on PATH — bailing before merge (LLM refresh failed)" >&2
    exit 6
  fi
  # POSIX-portable timeout: spawn claude in background, kill if it
  # outlives TIMEOUT_SECS. macOS has no `timeout` builtin and `gtimeout`
  # may not be installed.
  (
    COACH_DISABLE=1 "$CLAUDE_BIN" -p "/insights" > /dev/null 2>&1
  ) &
  CLAUDE_PID=$!
  SECS=0
  TIMED_OUT=0
  while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    sleep 2
    SECS=$((SECS + 2))
    if [[ "$SECS" -ge "$TIMEOUT_SECS" ]]; then
      kill -TERM "$CLAUDE_PID" 2>/dev/null
      sleep 2
      kill -KILL "$CLAUDE_PID" 2>/dev/null
      TIMED_OUT=1
      break
    fi
  done
  wait "$CLAUDE_PID" 2>/dev/null
  CLAUDE_RC=$?
  if [[ "$TIMED_OUT" -eq 1 ]]; then
    echo "claude -p /insights timed out after ${TIMEOUT_SECS}s — bailing before merge (LLM refresh failed)" >&2
    exit 6
  fi
  if [[ "$CLAUDE_RC" -ne 0 ]]; then
    echo "claude -p /insights exited rc=$CLAUDE_RC — bailing before merge (LLM refresh failed)" >&2
    exit 6
  fi
fi

# --- Step 2: aggregate facets → detections JSON ----------------------------
DET="$(mktemp /tmp/coach-weekly-detections-XXXXXX)"
trap 'rm -f "$DET"' EXIT

COACH_FACETS_DIR="$FACETS_DIR" "$PY" "$SCRIPT_DIR/aggregate_facets.py" \
  --window-days "$THROTTLE_DAYS" \
  > "$DET"
AGG_RC=$?
if [[ "$AGG_RC" -eq 3 ]]; then
  # Aggregator's EXIT_NO_EVIDENCE: n_sessions == 0 in the requested
  # window. Treat the same as any other "bail before merge" case —
  # don't merge, don't touch the throttle marker. The next session
  # retries from a fresh facets read. Distinct wrapper exit 7 so ops
  # can see "no evidence" vs "aggregator crashed" in logs.
  echo "no current-window evidence (n_sessions=0) — bailing before merge" >&2
  exit 7
fi
if [[ "$AGG_RC" -ne 0 ]]; then
  # Aggregator failure → bail BEFORE merge and BEFORE touching the
  # throttle marker. A nonzero aggregator may have written a partial
  # or empty $DET; merging that as `[]` would commit a clean-evidence
  # run that didn't actually happen, prematurely advancing debounce/
  # graduation streaks and consuming the weekly cadence. Re-running
  # the wrapper next session will retry from a fresh facets read.
  echo "aggregate_facets.py failed (rc=$AGG_RC) — bailing before merge" >&2
  exit 5
fi
N_DETS=$("$PY" - "$DET" <<'PY'
import json, sys
try:
    print(len(json.load(open(sys.argv[1]))))
except Exception:
    print(-1)
PY
)
if [[ "$N_DETS" -lt 0 ]]; then
  # Aggregator exited 0 but produced unparseable JSON → still bail.
  # Same reasoning as the rc check above; an empty/garbled $DET
  # cannot be merged safely.
  echo "aggregate_facets.py produced unparseable output — bailing before merge" >&2
  exit 5
fi
echo "detections=$N_DETS"

# --- Step 3: dry-run short-circuit -----------------------------------------
if [[ "$DRY_RUN" -eq 1 ]]; then
  cat "$DET"
  echo "(dry-run; merge skipped, throttle marker unchanged)"
  exit 0
fi

# --- Step 4: merge ---------------------------------------------------------
"$PY" "$SCRIPT_DIR/merge.py" \
  --profile    "$COACH_DIR/profile.yaml" \
  --changelog  "$COACH_DIR/changelog.md" \
  --lock       "$COACH_DIR/.lock" \
  --detections "$DET" \
  --run-id     "$RUN_ID" || {
    echo "merge.py failed" >&2
    exit 4
  }

# --- Step 5: commit + throttle marker --------------------------------------
( cd "$COACH_DIR" && git add -A && git commit -q -m "$RUN_ID" ) || true
touch "$THROTTLE_MARKER"

echo "done"
