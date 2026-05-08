#!/usr/bin/env python3
"""Aggregator: /insights facets/*.json sidecars → Coach detections JSON.

Pure Python, no LLM. Mirrors analyze.py's threshold-based emit pattern but
sources from Anthropic's structured `/insights` output rather than redacted
transcripts. The LLM has already done the per-session classification —
this script just counts stable enum keys across the window and applies
threshold rules.

Inputs:
  --facets-dir DIR   default: ~/.claude/usage-data/facets/
  --window-days N    default: 7
  --cap N            max detections to emit (default: 8)

Output:
  stdout: JSON array of detections (matches merge.py --detections schema)
  stderr: one-line summary "n_sessions=N detections=M"

Thresholds:
  - friction_counts.* keys appearing in ≥25% of sessions → negative detection
  - primary_success == key appearing in ≥60% of sessions → positive detection

Detection id derivation: enum key with `_` → `-` (e.g.
`misunderstood_request` → `misunderstood-request`). Stable across runs by
Anthropic's data contract — see plan §Key findings (Agent 1).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

NEGATIVE_THRESHOLD = 0.25
POSITIVE_THRESHOLD = 0.60
DEFAULT_CAP = 8
EXAMPLE_CAP = 3
EXAMPLE_MAXLEN = 120
DEFAULT_WINDOW_DAYS = 7

# Distinct CLI exit code for "no current-window evidence" — n_sessions == 0
# in the requested window. The wrapper (insights-llm.sh) translates this to
# its own exit 7. An empty detections list with zero sessions is "no
# evidence" and must NOT advance absence-based streaks at merge time;
# empty detections WITH n_sessions > 0 IS valid (a clean week) and merges
# normally. See plan-the-fixes-and-partitioned-squirrel.md §Commit 2.
EXIT_NO_EVIDENCE = 3

# Conservative example redaction — these sidecars are Anthropic-side
# summaries, but the prose may still echo back filenames the user typed.
# Strip path-like and file-extension-like tokens before storing in
# profile.yaml.
_PATH_RE = re.compile(r"(?:/|\\)[\w./\\-]+")
_FILE_EXT_RE = re.compile(
    r"\b[\w-]+\.(?:py|js|ts|tsx|jsx|mjs|cjs|md|markdown|sh|bash|zsh|"
    r"yaml|yml|toml|json|html|css|scss|sass|rs|go|java|cpp|hpp|h|c|"
    r"rb|sql|env|ini|conf|cfg|log|txt)\b",
    re.IGNORECASE,
)


def _redact_example(s: str) -> str:
    s = _PATH_RE.sub("[path]", s)
    s = _FILE_EXT_RE.sub("[file]", s)
    return s


def _kebab(key: str) -> str:
    return str(key).replace("_", "-").lower()


def _trim(s: str, maxlen: int = EXAMPLE_MAXLEN) -> str:
    s = (s or "").strip()
    if len(s) <= maxlen:
        return s
    # Cut on a word boundary if possible.
    cut = s[:maxlen].rsplit(" ", 1)[0]
    return cut if len(cut) >= maxlen // 2 else s[:maxlen]


def _iter_facets(facets_dir: Path, cutoff_ts: float):
    """Yield (path, parsed_json) for facets newer than cutoff_ts.

    Skips bad JSON silently. Mirrors analyze.py's tolerant read pattern.
    """
    if not facets_dir.exists() or not facets_dir.is_dir():
        return
    for p in facets_dir.iterdir():
        if not p.name.endswith(".json"):
            continue
        try:
            if p.stat().st_mtime < cutoff_ts:
                continue
        except OSError:
            continue
        try:
            data = json.loads(p.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        yield p, data


def aggregate(facets_dir: Path, window_days: int, cap: int) -> list[dict]:
    cutoff_ts = time.time() - window_days * 86400

    sessions: list[dict] = []
    for _, data in _iter_facets(facets_dir, cutoff_ts):
        sessions.append(data)

    n_sessions = len(sessions)
    if n_sessions == 0:
        return []

    # --- Negative side: friction_counts.* enum keys ---------------------
    # Count distinct sessions where each friction key has count ≥ 1.
    friction_session_counts: dict[str, int] = {}
    friction_examples: dict[str, list[str]] = {}
    for s in sessions:
        fc = s.get("friction_counts") or {}
        if not isinstance(fc, dict):
            continue
        seen_in_session: set[str] = set()
        for key, count in fc.items():
            if not isinstance(count, (int, float)) or count < 1:
                continue
            seen_in_session.add(str(key))
        if not seen_in_session:
            continue
        detail = s.get("friction_detail")
        for key in seen_in_session:
            friction_session_counts[key] = friction_session_counts.get(key, 0) + 1
            if isinstance(detail, str) and detail.strip():
                friction_examples.setdefault(key, []).append(detail)

    # --- Positive side: primary_success enum value ----------------------
    success_session_counts: dict[str, int] = {}
    success_examples: dict[str, list[str]] = {}
    for s in sessions:
        ps = s.get("primary_success")
        if not isinstance(ps, str) or not ps:
            continue
        success_session_counts[ps] = success_session_counts.get(ps, 0) + 1
        summary = s.get("brief_summary")
        if isinstance(summary, str) and summary.strip():
            success_examples.setdefault(ps, []).append(summary)

    detections: list[dict] = []

    for key, hits in friction_session_counts.items():
        ratio = hits / n_sessions
        if ratio < NEGATIVE_THRESHOLD:
            continue
        det_id = _kebab(key)
        examples = []
        seen_ex: set[str] = set()
        for raw in friction_examples.get(key, []):
            ex = _trim(_redact_example(raw))
            if not ex or ex in seen_ex:
                continue
            seen_ex.add(ex)
            examples.append(ex)
            if len(examples) >= EXAMPLE_CAP:
                break
        detections.append({
            "id": det_id,
            "name": det_id.replace("-", " "),
            "direction": "negative",
            "nudge": (
                f"In {hits} of {n_sessions} sessions in the last "
                f"{window_days} days, /insights flagged "
                f"{det_id.replace('-', ' ')}."
            ),
            "examples": examples,
            "priority": 2,
            "source": "insights-weekly",
            "ratio": round(ratio, 3),
            "n_sessions": n_sessions,
        })

    for key, hits in success_session_counts.items():
        ratio = hits / n_sessions
        if ratio < POSITIVE_THRESHOLD:
            continue
        det_id = _kebab(key)
        examples = []
        seen_ex: set[str] = set()
        for raw in success_examples.get(key, []):
            ex = _trim(_redact_example(raw))
            if not ex or ex in seen_ex:
                continue
            seen_ex.add(ex)
            examples.append(ex)
            if len(examples) >= EXAMPLE_CAP:
                break
        detections.append({
            "id": det_id,
            "name": det_id.replace("-", " "),
            "direction": "positive",
            "nudge": (
                f"In {hits} of {n_sessions} sessions in the last "
                f"{window_days} days, /insights tagged "
                f"{det_id.replace('-', ' ')} as the primary success."
            ),
            "examples": examples,
            "priority": 2,
            "source": "insights-weekly",
            "ratio": round(ratio, 3),
            "n_sessions": n_sessions,
        })

    # Schema-validate: id present + direction in {positive, negative}.
    valid = [
        d for d in detections
        if d.get("id") and d.get("direction") in ("positive", "negative")
    ]

    # Cap. Sort by ratio descending so the strongest signals win when
    # the cap bites.
    valid.sort(key=lambda d: d.get("ratio", 0), reverse=True)
    return valid[:cap]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--facets-dir",
        type=Path,
        default=None,
        help="Directory of facets/*.json sidecars (default: $HOME/.claude/usage-data/facets/)",
    )
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    args = ap.parse_args()

    facets_dir = args.facets_dir
    if facets_dir is None:
        env_dir = os.environ.get("COACH_FACETS_DIR")
        if env_dir:
            facets_dir = Path(env_dir)
        else:
            facets_dir = Path.home() / ".claude" / "usage-data" / "facets"

    detections = aggregate(facets_dir, args.window_days, args.cap)
    n_sessions = sum(
        1 for _ in _iter_facets(facets_dir, time.time() - args.window_days * 86400)
    )
    sys.stderr.write(
        f"facets_dir={facets_dir} n_sessions={n_sessions} "
        f"detections={len(detections)}\n"
    )
    if n_sessions == 0:
        # No evidence in the window. Refuse to emit detections so the
        # wrapper bails before merge — an empty list merged here would
        # be treated as a clean evidence pass and advance absence-based
        # streaks on no data. NOTE: stdout is intentionally empty so a
        # caller that ignores the exit code and pipes stdout into merge
        # gets a parse error rather than a silent `[]` merge.
        sys.stderr.write(
            f"no sessions in last {args.window_days} days — refusing to "
            f"emit detections; weekly path will retry next session\n"
        )
        return EXIT_NO_EVIDENCE
    sys.stdout.write(json.dumps(detections, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
