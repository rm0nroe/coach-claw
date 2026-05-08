#!/bin/bash
# Headless Coach insights pass — the deterministic cron path.
#
# Does NOT go through the claude CLI — runs the analyzer directly for speed
# and reliability (no LLM cold-start, no -p slash-command routing). The
# interactive `/coach-insights` skill is the on-demand counterpart (it
# delegates to Claude Code's built-in `/insights` for LLM-native analysis);
# this script is what launchd/cron invokes daily.
#
# The script's filename stays `insights.sh` for launchd-plist stability —
# the plist registered in v0.1+ has this path baked in, so renaming would
# force every existing user to re-run `install-launchd.sh` on upgrade.
#
# Usage: insights.sh [WINDOW]     (default 1d; also accepts 7d, 30d, etc.)
#
# Cross-platform notes:
#  - Resolves `python3` from PATH (no /usr/bin/python3 hardcode) so it works
#    on Homebrew / pyenv / system Python.
#  - Computes the since-time via Python so we don't depend on BSD `date -v`
#    (macOS) vs GNU `date -d` (Linux) flag availability.

set -uo pipefail

# Plugin-context PATH wedge: when invoked under a plugin install
# (CLAUDE_PLUGIN_DATA env var set + venv exists), prepend the plugin's
# venv bin/ to PATH so `python3` resolves to the venv interpreter that
# has PyYAML. CLI users never have CLAUDE_PLUGIN_DATA set; no-op for
# them. Currently the daily cron is CLI-only (the plugin nudges users
# to install it via `npx coach-claw launchd`), so this is defensive —
# but cheap, and unblocks plugin-only cron once we ship that path.
if [[ -n "${CLAUDE_PLUGIN_DATA:-}" && -x "$CLAUDE_PLUGIN_DATA/venv/bin/python3" ]]; then
  export PATH="$CLAUDE_PLUGIN_DATA/venv/bin:$PATH"
fi

WINDOW="${1:-1d}"
COACH_DIR="$HOME/.claude/coach"
RUN_ID="insights-$(date -u +%Y%m%dT%H%M%SZ)"

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "python3 not found in PATH" >&2
  exit 2
fi

# Filter transcripts by mtime in Python so we don't depend on BSD
# `find -newermt`, which interprets bare timestamps in the host's
# *local* timezone — wrong on every non-UTC host. insights_window.py
# does the cutoff math against an explicit UTC-aware datetime and
# compares to POSIX st_mtime, so the result is TZ-independent.
TRANSCRIPTS=()
TRANSCRIPT_OUTPUT="$("$PY" "$COACH_DIR/bin/insights_window.py" "$HOME/.claude/projects" "$WINDOW")"
WINDOW_STATUS=$?
if [[ "$WINDOW_STATUS" -ne 0 ]]; then
  exit "$WINDOW_STATUS"
fi
while IFS= read -r line; do
  [[ -n "$line" ]] && TRANSCRIPTS+=("$line")
done <<< "$TRANSCRIPT_OUTPUT"

N="${#TRANSCRIPTS[@]}"
echo "run_id=$RUN_ID window=$WINDOW transcripts=$N"

# Analyzer run (or empty detections if no transcripts)
DET="$(mktemp /tmp/coach-detections-XXXXXX)"
USED_JSON="$(mktemp /tmp/coach-skills-used-XXXXXX)"
HINTS_JSON="$(mktemp /tmp/coach-skill-hints-XXXXXX)"
DELTA_JSON="$(mktemp /tmp/coach-skills-by-project-delta-XXXXXX)"
EFFECTIVE_JSON="$(mktemp /tmp/coach-skills-by-project-effective-XXXXXX)"
trap 'rm -f "$DET" "$USED_JSON" "$HINTS_JSON" "$DELTA_JSON" "$EFFECTIVE_JSON"' EXIT

if [[ "$N" -eq 0 ]]; then
  echo "[]" > "$DET"
  echo "{}" > "$USED_JSON"
  echo "{}" > "$DELTA_JSON"
  echo "no transcripts in window"
else
  # Delegate to the analyzer — it handles redaction internally.
  ANALYSIS="$("$PY" "$COACH_DIR/bin/analyze.py" "${TRANSCRIPTS[@]}")" || {
    echo "analyzer failed" >&2
    exit 3
  }
  "$PY" - "$ANALYSIS" "$DET" "$USED_JSON" "$DELTA_JSON" <<'PY'
import json, sys
analysis_json, det_path, used_path, delta_path = sys.argv[1:5]
d = json.loads(analysis_json)
detections = d.get("detections", []) or []
summary = d.get("summary") or {}
with open(det_path, "w") as f:
    json.dump(detections, f, indent=2)
with open(used_path, "w") as f:
    json.dump(summary.get("skills_used", {}) or {}, f)
with open(delta_path, "w") as f:
    json.dump(summary.get("skills_by_project", {}) or {}, f)
print(f"detections={len(detections)} summary={summary}")
PY
fi

# Build the EFFECTIVE skills_by_project tempfile = profile's existing
# rolling accumulator + this run's delta. skill_inventory.py reads this
# to infer per-skill project scope; merge.py separately receives just
# the delta and accumulates it into the profile after.
"$PY" - "$COACH_DIR/profile.yaml" "$DELTA_JSON" "$EFFECTIVE_JSON" <<'PY'
import json, sys
from pathlib import Path
profile_path, delta_path, effective_path = (Path(p) for p in sys.argv[1:4])
existing: dict = {}
try:
    import yaml
    data = yaml.safe_load(profile_path.read_text()) if profile_path.exists() else {}
    if isinstance(data, dict):
        raw = data.get("skills_by_project") or {}
        if isinstance(raw, dict):
            for proj, skills in raw.items():
                if isinstance(skills, dict):
                    existing[str(proj)] = {str(k): int(v) for k, v in skills.items()
                                            if isinstance(v, (int, float))}
except Exception:
    existing = {}
try:
    delta = json.loads(delta_path.read_text() or "{}")
    if not isinstance(delta, dict):
        delta = {}
except Exception:
    delta = {}
effective: dict = {p: dict(s) for p, s in existing.items()}
for proj, skills in delta.items():
    if not isinstance(skills, dict):
        continue
    bucket = effective.setdefault(str(proj), {})
    for sid, count in skills.items():
        if isinstance(count, (int, float)):
            bucket[str(sid)] = bucket.get(str(sid), 0) + int(count)
effective_path.write_text(json.dumps(effective))
PY

# Build skill_hints snapshot (installed ∖ used-in-window)
"$PY" "$COACH_DIR/bin/skill_inventory.py" \
  --used-json "$USED_JSON" \
  --skills-by-project "$EFFECTIVE_JSON" \
  > "$HINTS_JSON" || {
    echo "[]" > "$HINTS_JSON"
  }
N_HINTS=$("$PY" - "$HINTS_JSON" <<'PY'
import json, sys
print(len(json.load(open(sys.argv[1]))))
PY
)
echo "skill_hints=$N_HINTS"

# Apply merge (safeguards + atomic write + changelog)
"$PY" "$COACH_DIR/bin/merge.py" \
  --profile                  "$COACH_DIR/profile.yaml" \
  --changelog                "$COACH_DIR/changelog.md" \
  --lock                     "$COACH_DIR/.lock" \
  --detections               "$DET" \
  --skill-hints              "$HINTS_JSON" \
  --skills-by-project-delta  "$DELTA_JSON" \
  --run-id                   "$RUN_ID"

# Git auto-commit (no-op if nothing changed)
( cd "$COACH_DIR" && git add -A && git commit -q -m "insights $RUN_ID" ) || true

echo "done"
