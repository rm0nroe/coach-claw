---
description: "Run an LLM-driven Coach insights pass — refreshes /insights facets, aggregates structured friction/wins, merges into profile. Usage: /coach-claw:coach-insights [--dry-run]"
disable-model-invocation: true
---

Thin wrapper around `${CLAUDE_PLUGIN_ROOT}/bin/insights-llm.sh --force`. The
underlying script (also fired automatically by the SessionStart hook on
a 7-day cadence) refreshes the `/insights` facets sidecars, aggregates
their stable enum keys deterministically, and hands detections to
`merge.py`. `--force` bypasses the 7-day cooldown so a manual run
always does work.

## What this does

1. Spawns `claude -p "/insights"` with `COACH_DISABLE=1` to refresh
   `~/.claude/usage-data/facets/<uuid>.json` sidecars. The CLI's stdout
   is discarded — we run it for the side effect on disk only.
2. Pipes the facets directory through `aggregate_facets.py` to convert
   stable enum keys (`friction_counts.*`, `primary_success`) into
   detections JSON with kebab-case ids (`misunderstood_request` →
   `misunderstood-request`). Threshold-gated: ≥25% of sessions for
   negatives, ≥60% for positives. Capped at 8 detections per run.
3. Hands detections to `merge.py` with `--run-id "insights-weekly-<ts>"`
   so downstream consumers can distinguish from the daily deterministic
   path's `insights-<ts>` runs.
4. Auto-commits the profile change and touches
   `~/.claude/coach/.last_weekly_insights` to throttle the next
   automatic trigger.

## Privacy

- **Daily cron path: local-only, zero network.** `analyze.py +
  redact.py` over redacted transcripts. Zero LLM cost. (Cron is
  managed by the npm CLI distribution; the plugin nudges users to
  install it if absent.)
- **Weekly path + on-demand `/coach-claw:coach-insights`:** triggers
  Anthropic-side `/insights` once per 7 days (via `claude -p`) to
  refresh structured facets data. Coach itself does not independently
  upload transcripts; the nested `/insights` refresh is an
  Anthropic-side Claude Code operation that runs inside the user's
  existing authenticated session and writes only to local sidecar
  files. Coach reads those local `facets/*.json` files. The LLM
  call's output is discarded; only the sidecar JSON refresh matters.
  `profile.yaml` stays local.

## Arguments

- `--dry-run` (optional): aggregate facets and print the detections
  JSON without invoking `merge.py` or touching the throttle marker.

## Steps

```bash
${CLAUDE_PLUGIN_ROOT}/bin/insights-llm.sh --force "$@"
```

That's it. Pass `--dry-run` through if the user supplied it.

`insights-llm.sh` self-detects the plugin venv via `CLAUDE_PLUGIN_DATA`
and prepends it to PATH at startup, so its internal `python3` calls
(aggregate_facets.py, merge.py) resolve PyYAML from the venv on a
fresh box. No skill-side wrapper needed.

Capture the script's stdout, then summarize for the user:

```
/coach-claw:coach-insights <run_id>
  detections: <N>
  → <changelog line from merge.py stdout>
```

If `--dry-run`, report the dry-run banner instead and skip the changelog
line.

## Rules

- **Never invent detections.** Zero is a valid output. The aggregator
  emits `[]` when no enum key crosses its threshold; do not pad.
- **Never edit `profile.yaml` directly.** Only `merge.py` mutates it.
- **No translation, no fuzzy matching.** The aggregator consumes
  facets enum keys 1:1 (Anthropic's data contract), so the manual
  path and the auto-spawned weekly path always emit the same ids
  for the same evidence.
- **One run per `RUN_ID`.** The script generates a unique
  `insights-weekly-<ts>` per invocation; do not re-run with the same
  id.
