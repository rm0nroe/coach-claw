#!/usr/bin/env python3
"""
Structural behavior analyzer — the deterministic cron path.

Reads redacted session transcripts and extracts DETERMINISTIC signals only
(tool-use counts, timing, presence/absence of planning artifacts). Never
emits content quotes — strictly aggregated counts.

As of v0.5.0 this is invoked **only by the daily deterministic
scheduled runner** (`run-insights.sh` → `insights.sh`). The on-demand
`/coach-insights` skill and the SessionStart-triggered weekly path
both delegate to `insights-llm.sh`, which invokes Claude Code's
built-in `/insights` for the side effect of refreshing
`facets/*.json` sidecars and then aggregates those structured
sidecars deterministically via `aggregate_facets.py` (no prose
translation — facets enum keys are stable kebab/snake-case slugs by
Anthropic's data contract). Two distinct paths with two distinct
IO contracts.

Input: space-separated transcript paths as argv
Output: JSON on stdout with {n_sessions, detections:[...], summary:{...}}
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from redact import redact
from scoring import matches_action


def _project_name_from_slug(slug: str) -> str:
    """Convert a Claude Code transcript dir name into the user-readable
    project name. Claude Code encodes a session's cwd as a slug where
    slashes become dashes, e.g. ``-Users-alice-Desktop-dev-widget``.
    The conservative recovery is the last dash-segment.

    Limitation: hyphenated original project names (e.g. ``acme-app``)
    collapse to the last segment (``app``). The hook's tokenizer already
    splits cwd anchors on dashes, so partial matches still fire (cwd
    anchor ``{acme, app}`` ∩ skill.projects token set ``{app}`` is
    non-empty). Users who want the full hyphenated form on a tagged
    skill can declare it in SKILL.md frontmatter; explicit `projects:`
    supersedes inference everywhere.
    """
    if not slug:
        return ""
    return slug.rstrip("-").split("-")[-1].lower()


def parse_ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _iter_redacted_records(path: Path):
    """Yield JSON records after redacting each JSONL line before parsing."""
    try:
        fh = path.open(errors="replace")
    except Exception:
        return
    with fh:
        for line in fh:
            if not line.strip():
                continue
            redacted = redact(line)
            try:
                yield json.loads(redacted)
            except Exception:
                continue


def analyze_session(path: Path) -> dict | None:
    try:
        records = _iter_redacted_records(path)
    except Exception:
        return None

    sig = {
        "path": str(path),
        "session_hash": path.stem[:8],
        "project": path.parent.name,
        "tool_counts": Counter(),
        "user_turns": 0,
        "assistant_turns": 0,
        "first_ts": None,
        "last_ts": None,
        "first_user_ts": None,
        "first_edit_ts": None,
        "first_edit_idx": None,
        "last_edit_idx": None,
        "first_plan_ts": None,
        "first_plan_idx": None,
        "first_test_idx": None,
        "last_test_idx": None,
        "first_commit_idx": None,
        "last_commit_idx": None,
        "first_read_idx": None,
        "first_search_idx": None,
        "task_create_count": 0,
        "exit_plan_count": 0,
        "edit_count": 0,
        "write_count": 0,
        "bash_count": 0,
        "read_count": 0,
        "grep_count": 0,
        "glob_count": 0,
        "agent_count": 0,
        "skill_count": 0,
        "skills_invoked": Counter(),
        "commit_count": 0,
        "test_run_count": 0,
        "has_any_test_run": False,
        "has_any_commit": False,
        "bash_rm_rf_count": 0,
    }

    event_idx = 0
    for rec in records:
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            if not sig["first_ts"] or ts < sig["first_ts"]:
                sig["first_ts"] = ts
            if not sig["last_ts"] or ts > sig["last_ts"]:
                sig["last_ts"] = ts

        rec_type = rec.get("type")
        msg = rec.get("message") or {}
        role = msg.get("role")

        if rec_type == "user" and role == "user":
            sig["user_turns"] += 1
            if ts and not sig["first_user_ts"]:
                sig["first_user_ts"] = ts

        if rec_type == "assistant" and role == "assistant":
            sig["assistant_turns"] += 1
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                idx = event_idx
                event_idx += 1
                name = block.get("name", "")
                sig["tool_counts"][name] += 1
                if name in ("Edit", "MultiEdit"):
                    sig["edit_count"] += 1
                    if ts and not sig["first_edit_ts"]:
                        sig["first_edit_ts"] = ts
                    if sig["first_edit_idx"] is None:
                        sig["first_edit_idx"] = idx
                    sig["last_edit_idx"] = idx
                elif name == "Write":
                    sig["write_count"] += 1
                    if ts and not sig["first_edit_ts"]:
                        sig["first_edit_ts"] = ts
                    if sig["first_edit_idx"] is None:
                        sig["first_edit_idx"] = idx
                    sig["last_edit_idx"] = idx
                elif name == "Plan":
                    if ts and not sig["first_plan_ts"]:
                        sig["first_plan_ts"] = ts
                    if sig["first_plan_idx"] is None:
                        sig["first_plan_idx"] = idx
                elif name in ("TaskCreate", "TodoWrite"):
                    sig["task_create_count"] += 1
                    if ts and not sig["first_plan_ts"]:
                        sig["first_plan_ts"] = ts
                    if sig["first_plan_idx"] is None:
                        sig["first_plan_idx"] = idx
                elif name == "ExitPlanMode":
                    sig["exit_plan_count"] += 1
                    if ts and not sig["first_plan_ts"]:
                        sig["first_plan_ts"] = ts
                    if sig["first_plan_idx"] is None:
                        sig["first_plan_idx"] = idx
                elif name == "Bash":
                    sig["bash_count"] += 1
                    if matches_action(block, "commit"):
                        sig["commit_count"] += 1
                        sig["has_any_commit"] = True
                        if sig["first_commit_idx"] is None:
                            sig["first_commit_idx"] = idx
                        sig["last_commit_idx"] = idx
                    if matches_action(block, "test_run"):
                        sig["test_run_count"] += 1
                        sig["has_any_test_run"] = True
                        if sig["first_test_idx"] is None:
                            sig["first_test_idx"] = idx
                        sig["last_test_idx"] = idx
                    cmd = (block.get("input") or {}).get("command", "")
                    if re.search(r"\brm\s+-rf?\b", cmd):
                        sig["bash_rm_rf_count"] += 1
                elif name == "Read":
                    sig["read_count"] += 1
                    if sig["first_read_idx"] is None:
                        sig["first_read_idx"] = idx
                elif name == "Grep":
                    sig["grep_count"] += 1
                    if sig["first_search_idx"] is None:
                        sig["first_search_idx"] = idx
                elif name == "Glob":
                    sig["glob_count"] += 1
                    if sig["first_search_idx"] is None:
                        sig["first_search_idx"] = idx
                elif name == "Agent":
                    sig["agent_count"] += 1
                elif name == "Skill":
                    sig["skill_count"] += 1
                    invoked = (block.get("input") or {}).get("skill")
                    if invoked:
                        sig["skills_invoked"][invoked] += 1

    if sig["first_user_ts"] and sig["first_edit_ts"]:
        sig["sec_first_user_to_first_edit"] = (
            sig["first_edit_ts"] - sig["first_user_ts"]
        ).total_seconds()
    else:
        sig["sec_first_user_to_first_edit"] = None

    sig["plan_before_edit"] = False
    if sig["first_plan_idx"] is not None and sig["first_edit_idx"] is not None:
        sig["plan_before_edit"] = sig["first_plan_idx"] <= sig["first_edit_idx"]
    elif sig["first_plan_idx"] is not None and sig["first_edit_idx"] is None:
        sig["plan_before_edit"] = True
    elif sig["first_plan_ts"] and sig["first_edit_ts"]:
        sig["plan_before_edit"] = sig["first_plan_ts"] <= sig["first_edit_ts"]
    elif sig["first_plan_ts"] and not sig["first_edit_ts"]:
        sig["plan_before_edit"] = True

    sig["tool_counts"] = dict(sig["tool_counts"])
    sig["skills_invoked"] = dict(sig["skills_invoked"])
    return sig


def _flat_and_per_project_skill_counts(
    sessions: list[dict],
) -> tuple[Counter, dict]:
    """Compute the flat ``skills_used`` Counter and the per-project
    ``skills_by_project`` breakdown from session signatures. Pulled out
    of aggregate() so it can run unconditionally — small windows
    (n < 3) skip pattern detection but still need to feed the rolling
    invocation history that drives skill_inventory's scope inference."""
    skills_used: Counter = Counter()
    skills_by_project: dict[str, dict[str, int]] = {}
    for s in sessions:
        proj = _project_name_from_slug(s.get("project") or "")
        invoked = s.get("skills_invoked") or {}
        for name, count in invoked.items():
            skills_used[name] += count
            if proj:
                bucket = skills_by_project.setdefault(proj, {})
                bucket[name] = bucket.get(name, 0) + count
    return skills_used, skills_by_project


def aggregate(sessions: list[dict]) -> tuple[list[dict], dict]:
    detections: list[dict] = []
    n = len(sessions)
    if n < 3:
        # Skip pattern detection (sample too small to be confident),
        # but still emit the per-project skill counts — the rolling
        # invocation history accumulates from windows of any size.
        skills_used, skills_by_project = _flat_and_per_project_skill_counts(sessions)
        return detections, {
            "n_sessions": n,
            "note": "too few sessions for detection",
            "skills_used": dict(skills_used),
            "skills_by_project": skills_by_project,
        }

    # 1. under-planning
    under_plan = [
        s for s in sessions
        if (s["edit_count"] + s["write_count"]) >= 5
        and s.get("sec_first_user_to_first_edit") is not None
        and s["sec_first_user_to_first_edit"] <= 120
        and not s["plan_before_edit"]
    ]
    if len(under_plan) >= 3:
        detections.append({
            "id": "under-planning",
            "name": "under-planning",
            "nudge": (
                f"Across {len(under_plan)} of {n} recent sessions, editing "
                "started within 2 minutes of the first user turn with no "
                "TaskCreate/TodoWrite/ExitPlanMode call preceding the first "
                "Edit or Write."
            ),
            "examples": [
                f"session {s['session_hash']} in {s['project']}: "
                f"{round(s['sec_first_user_to_first_edit'])}s to first edit, "
                f"{s['edit_count']+s['write_count']} total edits, no plan artifact"
                for s in under_plan[:3]
            ],
            "priority": 4,
            "source_session_ids": [s["session_hash"] for s in under_plan[:5]],
        })

    # 2. edits-without-testing
    no_tests = [s for s in sessions
                if (s["edit_count"] + s["write_count"]) >= 10
                and not s["has_any_test_run"]]
    if len(no_tests) >= 3:
        detections.append({
            "id": "edits-without-testing",
            "name": "edits without testing",
            "nudge": (
                f"Across {len(no_tests)} of {n} recent sessions with 10+ "
                "file mutations, zero pytest/jest/cargo/npm-test invocations "
                "were observed before session end."
            ),
            "examples": [
                f"session {s['session_hash']} in {s['project']}: "
                f"{s['edit_count']+s['write_count']} edits, 0 test runs"
                for s in no_tests[:3]
            ],
            "priority": 4,
            "source_session_ids": [s["session_hash"] for s in no_tests[:5]],
            # Explicit reward — keyword inference also produces this, but being
            # explicit makes the mapping robust if the nudge text is reworded.
            "reward_hint": {
                "action": "test_run",
                "xp": 2,
                "description": "test run (pytest / jest / cargo test / …)",
            },
        })

    # 3. commit-without-testing
    commit_no_test = [
        s for s in sessions
        if s["commit_count"] >= 1
        and not s["has_any_test_run"]
        and (s["edit_count"] + s["write_count"]) >= 5
    ]
    if len(commit_no_test) >= 3:
        detections.append({
            "id": "commit-without-testing",
            "name": "commit without testing",
            "nudge": (
                f"In {len(commit_no_test)} of {n} recent sessions, "
                "`git commit` ran after 5+ edits without any test command "
                "executing in the same session."
            ),
            "examples": [
                f"session {s['session_hash']} in {s['project']}: "
                f"{s['commit_count']} commit(s), {s['edit_count']+s['write_count']} edits, 0 tests"
                for s in commit_no_test[:3]
            ],
            "priority": 3,
            "source_session_ids": [s["session_hash"] for s in commit_no_test[:5]],
            "reward_hint": {
                "action": "test_run",
                "xp": 2,
                "description": "test run (pytest / jest / cargo test / …)",
            },
        })

    # 4. heavy-agent-delegation
    heavy_agent = [s for s in sessions if s["agent_count"] >= 8]
    if len(heavy_agent) >= 3:
        avg = sum(s["agent_count"] for s in heavy_agent) / len(heavy_agent)
        detections.append({
            "id": "heavy-agent-delegation",
            "name": "heavy subagent delegation",
            "nudge": (
                f"Across {len(heavy_agent)} of {n} recent sessions, "
                f"8+ Agent spawns were observed (avg {avg:.0f}/session)."
            ),
            "examples": [
                f"session {s['session_hash']} in {s['project']}: "
                f"{s['agent_count']} Agent spawns, "
                f"{s['edit_count']+s['write_count']} edits"
                for s in sorted(heavy_agent, key=lambda x: -x["agent_count"])[:3]
            ],
            "priority": 2,
            "source_session_ids": [s["session_hash"] for s in heavy_agent[:5]],
        })

    # 5. exploration-without-landing
    read_no_edit = [s for s in sessions
                    if s["read_count"] >= 15
                    and (s["edit_count"] + s["write_count"]) == 0
                    and s["assistant_turns"] >= 10]
    if len(read_no_edit) >= 3:
        detections.append({
            "id": "exploration-without-landing",
            "name": "exploration without landing",
            "nudge": (
                f"In {len(read_no_edit)} of {n} recent sessions, "
                "15+ Read tool calls occurred with zero Edit/Write calls, "
                "suggesting exploration that did not conclude with a change."
            ),
            "examples": [
                f"session {s['session_hash']} in {s['project']}: "
                f"{s['read_count']} reads, 0 edits, {s['assistant_turns']} assistant turns"
                for s in read_no_edit[:3]
            ],
            "priority": 2,
            "source_session_ids": [s["session_hash"] for s in read_no_edit[:5]],
            # Reward lands the exploration — landing a commit completes the tip.
            # Inference returns None for this pattern (no "test" keyword), so this
            # is a genuine addition, not just making the default explicit.
            "reward_hint": {
                "action": "commit",
                "xp": 1,
                "description": "git commit (land the change)",
            },
        })

    # 6. skipped-search-tools
    skipped_search = [s for s in sessions
                      if s["read_count"] >= 20
                      and (s["grep_count"] + s["glob_count"]) <= 2]
    if len(skipped_search) >= 3:
        detections.append({
            "id": "skipped-search-tools",
            "name": "skipped search tools",
            "nudge": (
                f"In {len(skipped_search)} of {n} recent sessions, "
                "20+ Read calls were made with ≤2 Grep/Glob calls — "
                "reading files without first narrowing by search."
            ),
            "examples": [
                f"session {s['session_hash']} in {s['project']}: "
                f"{s['read_count']} reads, {s['grep_count']}g+{s['glob_count']}gl search"
                for s in skipped_search[:3]
            ],
            "priority": 2,
            "source_session_ids": [s["session_hash"] for s in skipped_search[:5]],
        })

    def _edits(s: dict) -> int:
        return int(s.get("edit_count", 0) or 0) + int(s.get("write_count", 0) or 0)

    def _idx_before(a, b) -> bool:
        return a is not None and b is not None and a <= b

    def _idx_after(a, b) -> bool:
        return a is not None and b is not None and a > b

    def _positive_strength(
        *,
        relevant: list[dict],
        good: list[dict],
        id: str,
        name: str,
        nudge: str,
        example_fn,
        reward_hint: dict | None = None,
        priority: int = 2,
    ) -> None:
        # Strengths are intentionally stricter than weakness detections:
        # they need repeated evidence and a majority of the relevant window.
        if len(good) < 3 or not relevant:
            return
        if (len(good) / len(relevant)) < 0.60:
            return
        det = {
            "id": id,
            "name": name,
            "direction": "positive",
            "nudge": nudge,
            "examples": [example_fn(s) for s in good[:3]],
            "priority": priority,
            "source_session_ids": [s["session_hash"] for s in good[:5]],
        }
        if reward_hint:
            det["reward_hint"] = reward_hint
        detections.append(det)

    edit_sessions = [s for s in sessions if _edits(s) > 0]
    tests_after_edits = [
        s for s in edit_sessions
        if s.get("test_run_count", 0) >= 1
        and _idx_after(s.get("last_test_idx"), s.get("last_edit_idx"))
    ]
    _positive_strength(
        relevant=edit_sessions,
        good=tests_after_edits,
        id="tests-after-edits",
        name="tests after edits",
        nudge=(
            f"In {len(tests_after_edits)} of {len(edit_sessions)} edit sessions, "
            "a test command ran after the final file mutation."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"{_edits(s)} edits, {s['test_run_count']} test run(s) after edits"
        ),
        reward_hint={
            "action": "test_run",
            "xp": 2,
            "description": "test run (pytest / jest / cargo test / …)",
        },
    )

    plans_before_edits = [
        s for s in edit_sessions
        if s.get("plan_before_edit")
        or _idx_before(s.get("first_plan_idx"), s.get("first_edit_idx"))
    ]
    _positive_strength(
        relevant=edit_sessions,
        good=plans_before_edits,
        id="plans-before-edits",
        name="plans before edits",
        nudge=(
            f"In {len(plans_before_edits)} of {len(edit_sessions)} edit sessions, "
            "a TaskCreate/TodoWrite/ExitPlanMode/Plan artifact appeared before editing."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"planning preceded {_edits(s)} edit(s)"
        ),
    )

    commit_sessions = [s for s in sessions if s.get("commit_count", 0) >= 1]
    commits_gated_by_tests = [
        s for s in commit_sessions
        if s.get("test_run_count", 0) >= 1
        and _idx_before(s.get("last_test_idx"), s.get("last_commit_idx"))
    ]
    _positive_strength(
        relevant=commit_sessions,
        good=commits_gated_by_tests,
        id="commits-gated-by-tests",
        name="commits gated by tests",
        nudge=(
            f"In {len(commits_gated_by_tests)} of {len(commit_sessions)} commit sessions, "
            "a test command ran before the final git commit."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"{s['test_run_count']} test run(s) before {s['commit_count']} commit(s)"
        ),
        reward_hint={
            "action": "commit",
            "xp": 1,
            "description": "git commit after verification",
        },
    )

    read_sessions = [s for s in sessions if s.get("read_count", 0) >= 1]
    search_before_read = [
        s for s in read_sessions
        if (s.get("grep_count", 0) + s.get("glob_count", 0)) >= 1
        and _idx_before(s.get("first_search_idx"), s.get("first_read_idx"))
    ]
    _positive_strength(
        relevant=read_sessions,
        good=search_before_read,
        id="search-before-reading",
        name="search before reading",
        nudge=(
            f"In {len(search_before_read)} of {len(read_sessions)} read sessions, "
            "Grep/Glob narrowed the search before the first Read call."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"{s['grep_count']} Grep + {s['glob_count']} Glob before reading"
        ),
    )

    small_batch_verify = [
        s for s in edit_sessions
        if 1 <= _edits(s) <= 6
        and s.get("test_run_count", 0) >= 1
        and _idx_after(s.get("last_test_idx"), s.get("last_edit_idx"))
    ]
    _positive_strength(
        relevant=edit_sessions,
        good=small_batch_verify,
        id="small-batch-verify",
        name="small batch verify",
        nudge=(
            f"In {len(small_batch_verify)} of {len(edit_sessions)} edit sessions, "
            "changes stayed to six or fewer file mutations and ended with a test run."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"{_edits(s)} edits, then {s['test_run_count']} test run(s)"
        ),
        reward_hint={
            "action": "test_run",
            "xp": 2,
            "description": "test run after a small edit batch",
        },
    )

    safe_git = [
        s for s in commit_sessions
        if int(s.get("bash_rm_rf_count", 0) or 0) == 0
    ]
    _positive_strength(
        relevant=commit_sessions,
        good=safe_git,
        id="safe-git-hygiene",
        name="safe git hygiene",
        nudge=(
            f"In {len(safe_git)} of {len(commit_sessions)} commit sessions, "
            "git commit was observed without any rm -rf command in the same session."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"{s['commit_count']} commit(s), 0 rm -rf commands"
        ),
        reward_hint={
            "action": "commit",
            "xp": 1,
            "description": "git commit with safe shell hygiene",
        },
    )

    skill_sessions = [s for s in sessions if s.get("skill_count", 0) >= 1]
    _positive_strength(
        relevant=sessions,
        good=skill_sessions,
        id="effective-skill-use",
        name="effective skill use",
        nudge=(
            f"In {len(skill_sessions)} of {n} recent sessions, "
            "a slash-command skill or Skill tool was invoked during the work."
        ),
        example_fn=lambda s: (
            f"session {s['session_hash']} in {s['project']}: "
            f"{s['skill_count']} skill invocation(s)"
        ),
        reward_hint={
            "action": "skill_invoke",
            "xp": 1,
            "description": "skill invocation",
        },
    )

    skills_used, skills_by_project = _flat_and_per_project_skill_counts(sessions)

    summary = {
        "n_sessions": n,
        "total_edits": sum(s["edit_count"] + s["write_count"] for s in sessions),
        "total_bash": sum(s["bash_count"] for s in sessions),
        "total_agents": sum(s["agent_count"] for s in sessions),
        "sessions_with_tests": sum(1 for s in sessions if s["has_any_test_run"]),
        "sessions_with_plans": sum(1 for s in sessions if s["first_plan_ts"]),
        "skills_used": dict(skills_used),
        # Per-project breakdown for the skill_hints inference path.
        # Keys are user-readable project names; the rolling accumulator
        # lives in profile.yaml and is updated by merge.py each run.
        "skills_by_project": skills_by_project,
    }
    return detections, summary


def main():
    paths = [Path(p) for p in sys.argv[1:] if p.strip()]
    sessions = []
    for p in paths:
        sig = analyze_session(p)
        if sig and sig.get("assistant_turns", 0) > 0:
            sessions.append(sig)
    detections, summary = aggregate(sessions)
    print(json.dumps({"detections": detections, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
