"""
Shared transcript-scoring primitives for the Coach system.

One source of truth for action detection (regexes, tool-type matchers) and
per-action XP so that `stats.py`, `bank.py`, and the `coach-user-prompt.py`
hook all agree on what counts as what.

Exports:
  - TEST_RE, COMMIT_RE, COLLECT_ONLY_RE — position-anchored bash regexes
  - BASELINE_ACTIONS                     — {name -> xp} baseline reward table
  - matches_action(tool_use, action)     — shared per-tool-use matcher
  - score_transcript_with_breakdown()    — explainable uncapped/capped score
  - score_transcript(path, profile)      — returns capped session XP
      baseline XP for test_run / commit / skill_invoke, plus any
      additional XP for reward_hint actions found on active profile
      entries whose `action` is NOT already baseline (no double-counting).

Stats.py, bank.py, status.py, and the hook all call this module so their
definitions of "test run", "commit", "skill invoke", and dynamic actions do
not drift.

IMPORTANT: if you add a new action detector, extend ACTION_DETECTORS below
and also add a reward_hints.py heuristic entry so `/coach-insights` patterns can
auto-bind to it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


# --- Position-anchored regexes --------------------------------------------------
# Start-of-line or after ; && || |, with optional env-var or cd-prefix.
# Prevents false positives on "pytest" / "git commit" inside commit-message
# bodies.
TEST_RE = re.compile(
    r"(?:^|[;&|])\s*"
    r"(?:\w+=\S+\s+)*"
    r"(?:cd\s+\S+\s*&&\s*)?"
    r"(?:pytest|jest|vitest|mocha|rspec|phpunit|"
    r"cargo\s+test|go\s+test|pnpm\s+test|npm\s+test|bun\s+test|"
    r"yarn\s+test|mix\s+test)"
    r"\b"
)
COMMIT_RE = re.compile(
    r"(?:^|[;&|])\s*"
    r"(?:\w+=\S+\s+)*"
    r"(?:cd\s+\S+\s*&&\s*)?"
    r"git\s+commit\b"
)
# pytest --collect-only farms XP without running anything
COLLECT_ONLY_RE = re.compile(r"pytest\s+.*--co(llect)?-only")

# --- Baseline reward table -----------------------------------------------------
# Baseline actions always scored, regardless of profile contents. Matches
# what reward_hint can specify — if a pattern has `reward_hint: { action:
# test_run }`, that's the SAME +2 the baseline awards, not an additional one.
BASELINE_ACTIONS = {
    "test_run":     2,
    "commit":       1,
    "skill_invoke": 1,
}

SESSION_XP_CAP = 15


# --- Per-action detectors ------------------------------------------------------
# Each detector takes a tool_use dict (`{type, name, input}`) and returns
# True if that tool_use counts as one occurrence of this action.
def _detect_test_run(tu: dict) -> bool:
    if tu.get("name") != "Bash":
        return False
    cmd = (tu.get("input") or {}).get("command", "") or ""
    if COLLECT_ONLY_RE.search(cmd):
        return False
    return bool(TEST_RE.search(cmd))


def _detect_commit(tu: dict) -> bool:
    if tu.get("name") != "Bash":
        return False
    cmd = (tu.get("input") or {}).get("command", "") or ""
    return bool(COMMIT_RE.search(cmd))


# skill_invoke is counted by unique-skill-id set, not per-event, so it's
# handled specially in score_transcript — not in this dispatch.

def _detect_doc_write(tu: dict) -> bool:
    """Write/Edit on a markdown file. Reward for doc-skipping patterns."""
    name = tu.get("name", "")
    if name not in ("Write", "Edit", "MultiEdit"):
        return False
    path = (tu.get("input") or {}).get("file_path") or ""
    return isinstance(path, str) and path.endswith(".md")


# action-name → (event-detector-or-None, per-event-xp). None means
# "this action is scored specially in score_transcript" (e.g. skill_invoke
# which tallies unique skill ids).
ACTION_DETECTORS = {
    "test_run":     (_detect_test_run, 2),
    "commit":       (_detect_commit,   1),
    "skill_invoke": (None,             1),   # special: unique-set tally
    "doc_write":    (_detect_doc_write, 1),  # extension slot; /coach-insights can bind here
}


def matches_action(
    tool_use: dict,
    action: str,
    *,
    skill_id: str | None = None,
) -> bool:
    """Return True if one transcript tool_use satisfies an action.

    `skill_id` optionally narrows `skill_invoke` to one slash command / skill.
    Other actions are handled by ACTION_DETECTORS. Unknown actions are False
    rather than errors so hooks can fail closed.
    """
    if not isinstance(tool_use, dict):
        return False
    if action == "skill_invoke":
        if tool_use.get("name") not in ("SlashCommand", "Skill"):
            return False
        inp = tool_use.get("input") or {}
        sid = (inp.get("command") or inp.get("skill") or "").lstrip("/")
        if not sid:
            return False
        if skill_id:
            return sid == skill_id.lstrip("/")
        return True
    detector, _xp = ACTION_DETECTORS.get(action, (None, 0))
    if detector is None:
        return False
    return bool(detector(tool_use))


def _iter_tool_uses(path: Path):
    """Yield every tool_use dict from a transcript JSONL, tolerating junk.

    Intentional: skips the redact() pre-pass that analyze.py uses. The only
    outputs that escape this function (via score_transcript_with_breakdown,
    line ~243) are integer counts and skill_id slugs — no transcript bytes
    are returned or persisted. If the output shape ever expands to include
    user content, add the redact() pass to match analyze.py.
    """
    try:
        with path.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        yield item
    except Exception:
        return


def _dynamic_actions_from_profile(profile: dict) -> dict:
    """Return {action_name: xp} for reward_hint.action values present in
    profile that are NOT already baseline. Deduplicates — if two patterns
    reference the same action, it's still one scoring rule."""
    out: dict[str, int] = {}
    if not isinstance(profile, dict):
        return out
    for e in profile.get("entries", []) or []:
        if not isinstance(e, dict):
            continue
        h = e.get("reward_hint")
        if not isinstance(h, dict):
            continue
        action = h.get("action")
        xp = int(h.get("xp", 0) or 0)
        if not action or xp <= 0:
            continue
        if action in BASELINE_ACTIONS:
            continue   # already scored via baseline
        if action not in ACTION_DETECTORS:
            continue   # no detector registered; stats.py can't score it
        # If multiple patterns reference the same non-baseline action with
        # different xp values, keep the max — conservative upper bound.
        out[action] = max(out.get(action, 0), xp)
    return out


def score_transcript(path: Path, profile: dict | None = None) -> int:
    """Return capped session XP for a single transcript.

    Always scores the three baseline actions (test_run, commit, skill_invoke).
    If `profile` is provided, also scores any additional reward_hint actions
    registered in ACTION_DETECTORS. Total capped at SESSION_XP_CAP.
    """
    return int(score_transcript_with_breakdown(path, profile)["capped_xp"])


def score_transcript_with_breakdown(
    path: Path,
    profile: dict | None = None,
) -> dict:
    """Return an explainable session score for one transcript.

    Shape is intentionally simple for CLI renderers:
      tests, commits, skills_n, skills_list, dynamic_actions,
      raw_xp, capped_xp, capped.
    """
    test_runs = 0
    commits = 0
    skills: set[str] = set()
    dynamic_counts: dict[str, int] = {}
    dynamic_actions = _dynamic_actions_from_profile(profile or {})

    for tu in _iter_tool_uses(path):
        if matches_action(tu, "test_run"):
            test_runs += 1
        if matches_action(tu, "commit"):
            commits += 1
        if matches_action(tu, "skill_invoke"):
            sid = ((tu.get("input") or {}).get("command")
                   or (tu.get("input") or {}).get("skill") or "").lstrip("/")
            if sid:
                skills.add(sid)
        for action in dynamic_actions:
            if matches_action(tu, action):
                dynamic_counts[action] = dynamic_counts.get(action, 0) + 1

    dynamic_breakdown = {}
    for action, count in sorted(dynamic_counts.items()):
        xp_each = dynamic_actions[action]
        dynamic_breakdown[action] = {
            "count": count,
            "xp_each": xp_each,
            "xp": count * xp_each,
        }

    raw_xp = (
        test_runs * BASELINE_ACTIONS["test_run"]
        + commits * BASELINE_ACTIONS["commit"]
        + len(skills) * BASELINE_ACTIONS["skill_invoke"]
        + sum(item["xp"] for item in dynamic_breakdown.values())
    )

    return {
        "tests": test_runs,
        "commits": commits,
        "skills_n": len(skills),
        "skills_list": sorted(skills),
        "dynamic_actions": dynamic_breakdown,
        "available_dynamic_actions": dict(sorted(dynamic_actions.items())),
        "raw_xp": raw_xp,
        "capped_xp": min(raw_xp, SESSION_XP_CAP),
        "capped": raw_xp > SESSION_XP_CAP,
    }
