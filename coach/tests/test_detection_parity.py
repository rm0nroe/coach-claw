from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import analyze
import scoring
import stats


def _load_hook_module():
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    spec = importlib.util.spec_from_file_location("coach_user_prompt_parity", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tool_use(name: str, input_: dict) -> dict:
    return {"type": "tool_use", "name": name, "input": input_}


def _write_transcript(path: Path, tool_uses: list[dict]) -> None:
    lines = []
    for idx, tu in enumerate(tool_uses):
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": f"2026-01-01T00:00:{idx:02d}+00:00",
            "message": {
                "role": "assistant",
                "content": [tu],
            },
        }))
    path.write_text("\n".join(lines) + "\n")


def test_hook_completion_matcher_tracks_shared_scoring_actions(tmp_path: Path) -> None:
    hook = _load_hook_module()
    cases = [
        (_tool_use("Bash", {"command": "pytest --collect-only tests/"}), "test_run", None, False),
        (_tool_use("Bash", {"command": "mocha"}), "test_run", None, True),
        (_tool_use("Bash", {"command": "yarn test"}), "test_run", None, True),
        (_tool_use("Bash", {"command": "printf 'pytest in text only'"}), "test_run", None, False),
        (_tool_use("Bash", {"command": "printf 'git commit in text only'"}), "commit", None, False),
        (_tool_use("Skill", {"skill": "/update-docs"}), "skill_invoke", "update-docs", True),
        (_tool_use("Edit", {"file_path": "README.md"}), "doc_write", None, True),
    ]

    for tool_use, action, skill_id, expected in cases:
        assert scoring.matches_action(tool_use, action, skill_id=skill_id) is expected
        assert hook._tool_use_matches_action(tool_use, action, skill_id=skill_id) is expected


def test_stats_session_xp_delegates_to_shared_scoring(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        _tool_use("Bash", {"command": "pytest --collect-only tests/"}),
        _tool_use("Bash", {"command": "mocha"}),
        _tool_use("Bash", {"command": "yarn test"}),
        _tool_use("Bash", {"command": "printf 'pytest and git commit are text'"}),
        _tool_use("Bash", {"command": "git commit -m ok"}),
        _tool_use("Skill", {"skill": "/update-docs"}),
        _tool_use("Edit", {"file_path": "README.md"}),
    ])
    profile = {
        "entries": [{
            "id": "docs-drift",
            "reward_hint": {"action": "doc_write", "xp": 1, "description": "doc update"},
        }]
    }

    assert stats._session_xp_from_transcript(transcript, profile) == scoring.score_transcript(
        transcript, profile
    )
    breakdown = scoring.score_transcript_with_breakdown(transcript, profile)
    assert breakdown["tests"] == 2
    assert breakdown["commits"] == 1
    assert breakdown["skills_list"] == ["update-docs"]
    assert breakdown["dynamic_actions"]["doc_write"]["count"] == 1


def test_analyze_test_run_detection_tracks_shared_scoring(tmp_path: Path) -> None:
    commands = [
        "pytest --collect-only tests/",
        "mocha",
        "pnpm test",
        "yarn test",
        "printf 'pytest and git commit are just words'",
    ]
    transcript = tmp_path / "session.jsonl"
    _write_transcript(
        transcript,
        [_tool_use("Bash", {"command": cmd}) for cmd in commands],
    )

    sig = analyze.analyze_session(transcript)
    expected = sum(
        1
        for cmd in commands
        if scoring.matches_action(_tool_use("Bash", {"command": cmd}), "test_run")
    )
    assert sig["test_run_count"] == expected
    assert sig["has_any_test_run"] is True
