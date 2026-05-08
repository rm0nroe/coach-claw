from __future__ import annotations

import json

import yaml

import status


def test_status_splits_lifetime_xp_buckets(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "profile.yaml"
    ledger = tmp_path / "banked_sessions.json"
    changelog = tmp_path / "changelog.md"
    projects = tmp_path / "projects"
    projects.mkdir()
    ledger.write_text(json.dumps({"s1": {"xp": 12, "banked": 1}}))
    changelog.write_text("")
    profile.write_text(yaml.safe_dump({
        "entries": [{"id": "w1", "direction": "negative", "clean_streak_runs": 2}],
        "graduated": [{"id": "done"}],
        "archived": [{"id": "aged-out", "direction": "negative"}],
        "session_banked_xp": 4,
        "milestone_xp": 3,
        "manual_adjustments": 1,
    }))
    monkeypatch.setattr(status, "PROFILE", profile)
    monkeypatch.setattr(status, "LEDGER", ledger)
    monkeypatch.setattr(status, "CHANGELOG", changelog)
    monkeypatch.setattr(status, "PROJECTS", projects)

    status.main()

    out = capsys.readouterr().out
    assert "Lifetime (15 xp)" in out
    assert "graduated patterns (1 × 5)" in out
    assert "completed sessions (1 sessions at 10:1)" in out
    assert "mid-streak milestones" in out
    assert "manual adjustments" in out
    assert "1 retired" in out
    assert "1 archived" in out
    assert "banked from past sessions" not in out
