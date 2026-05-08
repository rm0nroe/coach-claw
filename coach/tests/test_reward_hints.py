"""reward_hints.py — keyword inference + explicit-vs-inference precedence."""
from __future__ import annotations

from reward_hints import infer_reward_hint, effective_reward_hint


def test_infer_test_run_from_id():
    entry = {"id": "edits-without-testing", "nudge": ""}
    hint = infer_reward_hint(entry)
    assert hint is not None
    assert hint["action"] == "test_run"
    assert hint["xp"] == 2


def test_infer_test_run_from_nudge_only():
    entry = {"id": "some-unrelated-id", "nudge": "User skipped tests after edits."}
    hint = infer_reward_hint(entry)
    assert hint is not None
    assert hint["action"] == "test_run"


def test_infer_commit_from_id():
    entry = {"id": "edits-without-committing", "nudge": ""}
    hint = infer_reward_hint(entry)
    assert hint is not None
    assert hint["action"] == "commit"
    assert hint["xp"] == 1


def test_infer_no_match_returns_none():
    entry = {"id": "random-pattern", "nudge": "Something with no keyword hit."}
    assert infer_reward_hint(entry) is None


def test_effective_explicit_wins_over_inference():
    # id would match test_run via keyword, but explicit hint should dominate
    entry = {
        "id": "edits-without-testing",
        "nudge": "",
        "reward_hint": {"action": "commit", "xp": 1, "description": "explicit override"},
    }
    hint = effective_reward_hint(entry)
    assert hint["action"] == "commit"
    assert hint["description"] == "explicit override"


def test_effective_falls_through_to_inference_when_invalid():
    # reward_hint present but invalid (missing action) → fall through
    entry = {"id": "edits-without-testing", "nudge": "", "reward_hint": {}}
    hint = effective_reward_hint(entry)
    assert hint["action"] == "test_run"


def test_infer_returns_a_copy_not_default():
    entry = {"id": "edits-without-testing", "nudge": ""}
    h1 = infer_reward_hint(entry)
    h1["xp"] = 99  # mutate caller's copy
    h2 = infer_reward_hint(entry)
    assert h2["xp"] == 2   # defaults unchanged
