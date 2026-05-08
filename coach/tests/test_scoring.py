"""scoring.py — baseline actions, SESSION_XP_CAP, dynamic reward_hint actions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scoring import (
    BASELINE_ACTIONS,
    SESSION_XP_CAP,
    matches_action,
    score_transcript,
    score_transcript_with_breakdown,
)


def _write_transcript(path: Path, tool_uses: list[dict]) -> None:
    """Write a minimal JSONL transcript with the given tool_uses."""
    lines = []
    for tu in tool_uses:
        lines.append(json.dumps({"message": {"content": [dict(tu, type="tool_use")]}}))
    path.write_text("\n".join(lines) + "\n")


def test_baseline_test_run_scoring(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "pytest tests/"}},
        {"name": "Bash", "input": {"command": "pytest tests/unit/"}},
    ])
    assert score_transcript(t, {}) == 2 * BASELINE_ACTIONS["test_run"]


def test_baseline_commit_scoring(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "git commit -m 'x'"}},
        {"name": "Bash", "input": {"command": "git commit -m 'y'"}},
        {"name": "Bash", "input": {"command": "git commit -m 'z'"}},
    ])
    assert score_transcript(t, {}) == 3 * BASELINE_ACTIONS["commit"]


def test_skill_invoke_counted_unique(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        {"name": "Skill", "input": {"skill": "/foo"}},
        {"name": "Skill", "input": {"skill": "/foo"}},        # duplicate → same unique
        {"name": "SlashCommand", "input": {"command": "/bar"}},
    ])
    # 2 unique skill ids (foo, bar) × 1 = 2
    assert score_transcript(t, {}) == 2 * BASELINE_ACTIONS["skill_invoke"]


def test_collect_only_pytest_ignored(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "pytest --collect-only tests/"}},
    ])
    assert score_transcript(t, {}) == 0


def test_shared_matcher_ignores_collect_only_pytest():
    tu = {
        "type": "tool_use",
        "name": "Bash",
        "input": {"command": "pytest --collect-only tests/"},
    }
    assert matches_action(tu, "test_run") is False


@pytest.mark.parametrize("command", ["mocha", "yarn test", "pnpm test", "vitest"])
def test_shared_matcher_accepts_supported_test_runners(command: str):
    tu = {"type": "tool_use", "name": "Bash", "input": {"command": command}}
    assert matches_action(tu, "test_run") is True


def test_commit_message_text_does_not_count_as_action():
    tu = {
        "type": "tool_use",
        "name": "Bash",
        "input": {"command": "printf 'run pytest and git commit after this'"},
    }
    assert matches_action(tu, "test_run") is False
    assert matches_action(tu, "commit") is False


def test_session_cap_enforced(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    # 20 test runs × 2 XP = 40 raw, capped at 15
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "pytest"}} for _ in range(20)
    ])
    assert score_transcript(t, {}) == SESSION_XP_CAP


def test_dynamic_action_from_profile(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        {"name": "Edit", "input": {"file_path": "docs/README.md"}},
        {"name": "Edit", "input": {"file_path": "docs/guide.md"}},
        {"name": "Edit", "input": {"file_path": "src/main.py"}},  # not md → ignored
    ])
    profile = {
        "entries": [{
            "id": "skipping-docs",
            "nudge": "",
            "reward_hint": {"action": "doc_write", "xp": 1, "description": "doc update"},
        }]
    }
    # 2 .md edits × 1 xp = 2
    assert score_transcript(t, profile) == 2


def test_breakdown_includes_dynamic_reward_hint_actions(tmp_path: Path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "mocha"}},
        {"name": "Edit", "input": {"file_path": "README.md"}},
    ])
    profile = {
        "entries": [{
            "id": "skipping-docs",
            "reward_hint": {"action": "doc_write", "xp": 1, "description": "doc update"},
        }]
    }
    got = score_transcript_with_breakdown(t, profile)
    assert got["tests"] == 1
    assert got["dynamic_actions"]["doc_write"] == {"count": 1, "xp_each": 1, "xp": 1}
    assert got["available_dynamic_actions"] == {"doc_write": 1}
    assert got["capped_xp"] == 3


def test_baseline_action_not_double_counted_via_reward_hint(tmp_path: Path):
    """reward_hint.action=test_run is BASELINE — must not double-count."""
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [{"name": "Bash", "input": {"command": "pytest"}}])
    profile = {
        "entries": [{
            "id": "edits-without-testing",
            "nudge": "",
            "reward_hint": {"action": "test_run", "xp": 2, "description": "test run"},
        }]
    }
    # Should still be exactly +2 (baseline), not +4
    assert score_transcript(t, profile) == 2
