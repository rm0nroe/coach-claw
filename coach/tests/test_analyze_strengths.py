from __future__ import annotations

import json

import analyze


def _session(
    *,
    session_hash: str = "abcd1234",
    edits: int = 0,
    writes: int = 0,
    tests: int = 0,
    commits: int = 0,
    reads: int = 0,
    grep: int = 0,
    glob: int = 0,
    skills: int = 0,
    rm_rf: int = 0,
    plan_before_edit: bool = False,
    first_plan_idx=None,
    first_edit_idx=None,
    last_edit_idx=None,
    first_test_idx=None,
    last_test_idx=None,
    first_commit_idx=None,
    last_commit_idx=None,
    first_read_idx=None,
    first_search_idx=None,
) -> dict:
    return {
        "project": "-Users-r-Desktop-dev-coach",
        "session_hash": session_hash,
        "tool_counts": {},
        "user_turns": 1,
        "assistant_turns": 5,
        "first_ts": None,
        "last_ts": None,
        "first_user_ts": None,
        "first_edit_ts": None,
        "first_plan_ts": None,
        "task_create_count": 0,
        "exit_plan_count": 0,
        "edit_count": edits,
        "write_count": writes,
        "bash_count": tests + commits,
        "commit_count": commits,
        "test_run_count": tests,
        "has_any_commit": commits > 0,
        "has_any_test_run": tests > 0,
        "bash_rm_rf_count": rm_rf,
        "read_count": reads,
        "grep_count": grep,
        "glob_count": glob,
        "agent_count": 0,
        "skill_count": skills,
        "skills_invoked": {},
        "sec_first_user_to_first_edit": None,
        "plan_before_edit": plan_before_edit,
        "first_plan_idx": first_plan_idx,
        "first_edit_idx": first_edit_idx,
        "last_edit_idx": last_edit_idx,
        "first_test_idx": first_test_idx,
        "last_test_idx": last_test_idx,
        "first_commit_idx": first_commit_idx,
        "last_commit_idx": last_commit_idx,
        "first_read_idx": first_read_idx,
        "first_search_idx": first_search_idx,
    }


def _strong_session(i: int) -> dict:
    return _session(
        session_hash=f"good{i}",
        edits=3,
        tests=1,
        commits=1,
        reads=2,
        grep=1,
        skills=1,
        plan_before_edit=True,
        first_plan_idx=0,
        first_search_idx=1,
        first_read_idx=2,
        first_edit_idx=3,
        last_edit_idx=3,
        first_test_idx=4,
        last_test_idx=4,
        first_commit_idx=5,
        last_commit_idx=5,
    )


def test_aggregate_emits_positive_strength_detections():
    detections, _summary = analyze.aggregate([_strong_session(i) for i in range(3)])

    by_id = {d["id"]: d for d in detections}
    expected = {
        "tests-after-edits",
        "plans-before-edits",
        "commits-gated-by-tests",
        "search-before-reading",
        "small-batch-verify",
        "safe-git-hygiene",
        "effective-skill-use",
    }

    assert expected <= set(by_id)
    assert all(by_id[eid]["direction"] == "positive" for eid in expected)
    assert by_id["tests-after-edits"]["reward_hint"]["action"] == "test_run"
    assert by_id["effective-skill-use"]["reward_hint"]["action"] == "skill_invoke"


def test_positive_strengths_require_repeated_majority_evidence():
    sessions = [
        _session(
            session_hash=f"good{i}",
            edits=2,
            tests=1,
            first_edit_idx=1,
            last_edit_idx=1,
            first_test_idx=2,
            last_test_idx=2,
        )
        for i in range(2)
    ]
    sessions += [
        _session(session_hash=f"bad{i}", edits=2, first_edit_idx=1, last_edit_idx=1)
        for i in range(3)
    ]

    detections, _summary = analyze.aggregate(sessions)

    assert "tests-after-edits" not in {d["id"] for d in detections}


def test_analyze_session_records_tool_order_for_strength_detectors(tmp_path):
    transcript = tmp_path / "session.jsonl"
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Plan", "input": {}},
                {"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "git commit -m ok"}},
                {"type": "tool_use", "name": "Skill", "input": {"skill": "design"}},
            ],
        },
    }
    transcript.write_text(json.dumps(record) + "\n")

    sig = analyze.analyze_session(transcript)

    assert sig is not None
    assert sig["first_plan_idx"] == 0
    assert sig["first_search_idx"] == 1
    assert sig["first_read_idx"] == 2
    assert sig["first_edit_idx"] == 3
    assert sig["last_test_idx"] == 4
    assert sig["last_commit_idx"] == 5
    assert sig["plan_before_edit"] is True
