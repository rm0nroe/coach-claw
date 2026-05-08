"""merge.py — graduation, mid-streak rewards, reward_hint precedence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

import merge


# --- helpers ----------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 4, 20, 0, 0, 0, tzinfo=timezone.utc)


def _blank_profile() -> dict:
    return {"schema_version": 1, "updated": None, "entries": [], "recent_runs": []}


@pytest.fixture
def _marker_cleanup(tmp_path, monkeypatch):
    """Redirect marker writes into tmp_path so tests don't pollute ~/.claude."""
    monkeypatch.setattr(merge, "GRADUATION_MARKER", tmp_path / "graduation.json")
    monkeypatch.setattr(merge, "STREAK_REWARD_MARKER", tmp_path / "streak_rewards.json")
    monkeypatch.setattr(merge, "REGRESSION_MARKER", tmp_path / "regression.json")
    yield tmp_path


# --- graduation: negative absence ------------------------------------------

def test_negative_graduation_fires_at_clean_streak_5(_marker_cleanup):
    """The P1 #1 fix: clean_streak_runs=4 + empty detections → graduate."""
    profile = _blank_profile()
    profile["entries"] = [{
        "id": "w1", "name": "weakness 1", "tier": "active", "direction": "negative",
        "confidence": 0.8, "priority": 3, "nudge": "x", "examples": [],
        "first_seen": "2026-03-01", "last_seen_in_run": "2026-04-01",
        "clean_streak_runs": 4, "positive_run_streak": 0,
        "source_runs": ["old"], "total_occurrences": 10,
    }]
    profile["recent_runs"] = ["r-a", "r-b", "r-c"]

    fragments = merge.merge(profile, detections=[], run_id="r-d", now=_now())

    assert profile["entries"] == []
    assert len(profile["graduated"]) == 1
    g = profile["graduated"][0]
    assert g["id"] == "w1"
    assert g["direction"] == "negative"
    assert g["graduated_reason"] == "absent-5-runs"
    assert any("🎓w1" in f for f in fragments)


def test_negative_absence_below_threshold_does_not_graduate(_marker_cleanup):
    profile = _blank_profile()
    profile["entries"] = [{
        "id": "w1", "name": "weakness 1", "tier": "active", "direction": "negative",
        # last_seen recent enough that confidence decay doesn't push below
        # RETIRE_BELOW — we're isolating the absence-graduation path here.
        "confidence": 0.9, "priority": 3, "nudge": "x", "examples": [],
        "first_seen": "2026-04-19", "last_seen_in_run": "2026-04-19",
        "clean_streak_runs": 2, "positive_run_streak": 0,
        "source_runs": ["old"], "total_occurrences": 10,
    }]
    merge.merge(profile, detections=[], run_id="r-d", now=_now())

    # Should tick to 3, still active
    assert len(profile["entries"]) == 1
    assert profile["entries"][0]["clean_streak_runs"] == 3
    assert profile.get("graduated", []) == []


def test_low_confidence_retirement_archives_without_graduation_xp(_marker_cleanup):
    """Confidence decay below RETIRE_BELOW is uncertainty, not mastery.
    It leaves the live list but must not award +5 or fire graduation UX."""
    profile = _blank_profile()
    profile["entries"] = [{
        "id": "w1", "name": "weakness 1", "tier": "active", "direction": "negative",
        # One day of decay pushes this below RETIRE_BELOW, while clean streak
        # remains far below the absence-graduation threshold.
        "confidence": 0.31, "priority": 3, "nudge": "x", "examples": [],
        "first_seen": "2026-04-19", "last_seen_in_run": "2026-04-19",
        "clean_streak_runs": 0, "positive_run_streak": 0,
        "source_runs": ["old"], "total_occurrences": 2,
    }]

    fragments = merge.merge(profile, detections=[], run_id="r-d", now=_now())

    assert profile["entries"] == []
    assert profile.get("graduated", []) == []
    assert len(profile["archived"]) == 1
    archived = profile["archived"][0]
    assert archived["id"] == "w1"
    assert archived["archive_reason"] == "low-confidence"
    assert profile["graduation_xp"] == 0
    assert any("archived:low-confidence" in f for f in fragments)
    assert not merge.GRADUATION_MARKER.exists()


# --- mid-streak rewards ----------------------------------------------------

def test_strength_mid_streak_reward_fires(_marker_cleanup):
    """The P1 #2 fix: positive entry re-detected → +1 XP banked at streak 2."""
    profile = _blank_profile()
    profile["entries"] = [{
        "id": "s1", "name": "strength 1", "tier": "probationary", "direction": "positive",
        "confidence": 0.6, "priority": 3, "nudge": "keep doing that", "examples": [],
        "first_seen": "2026-04-13", "last_seen_in_run": "2026-04-13",
        "positive_run_streak": 1, "clean_streak_runs": 0,
        "source_runs": ["r-a"], "total_occurrences": 1,
    }]
    profile["recent_runs"] = ["r-a"]

    merge.merge(
        profile,
        detections=[{"id": "s1", "name": "strength 1", "direction": "positive",
                     "priority": 3, "nudge": "keep doing that"}],
        run_id="r-b", now=_now(),
    )

    # Streak bumped 1 → 2, milestone_xp += 1
    assert profile["entries"][0]["positive_run_streak"] == 2
    assert profile["milestone_xp"] == 1
    assert profile["session_banked_xp"] == 0
    # Marker written with direction: positive
    marker_path = merge.STREAK_REWARD_MARKER
    assert marker_path.exists()
    data = json.loads(marker_path.read_text())
    assert data["rewards"][0]["direction"] == "positive"
    assert data["rewards"][0]["streak"] == 2
    assert data["rewards"][0]["xp_awarded"] == 1


def test_strength_graduation_at_streak_5_no_double_reward(_marker_cleanup):
    """Streak 4→5: graduates (+5) but NO mid-streak reward (5 not in schedule)."""
    profile = _blank_profile()
    profile["entries"] = [{
        "id": "s1", "name": "strength 1", "tier": "active", "direction": "positive",
        "confidence": 0.9, "priority": 3, "nudge": "", "examples": [],
        "first_seen": "2026-03-01", "last_seen_in_run": "2026-04-14",
        "positive_run_streak": 4, "clean_streak_runs": 0,
        "source_runs": ["r-a", "r-b", "r-c"], "total_occurrences": 4,
    }]
    profile["recent_runs"] = ["r-a", "r-b", "r-c"]

    merge.merge(
        profile,
        detections=[{"id": "s1", "name": "strength 1", "direction": "positive",
                     "priority": 3, "nudge": ""}],
        run_id="r-d", now=_now(),
    )

    # Graduated mastery — no mid-streak marker
    assert profile["entries"] == []
    assert len(profile["graduated"]) == 1
    assert profile["graduated"][0]["graduated_reason"] == "present-5-runs"
    assert profile.get("milestone_xp", 0) == 0
    assert profile.get("graduation_xp", 0) == 5
    assert not merge.STREAK_REWARD_MARKER.exists()


def test_negative_mid_streak_reward_schedule(_marker_cleanup):
    """+1/+1/+1/+2 across clean_streak ticks 1-4 for weaknesses."""
    profile = _blank_profile()
    profile["entries"] = [{
        "id": "w1", "name": "weakness", "tier": "active", "direction": "negative",
        "confidence": 0.8, "priority": 3, "nudge": "", "examples": [],
        "first_seen": "2026-03-01", "last_seen_in_run": "2026-04-01",
        "clean_streak_runs": 3, "positive_run_streak": 0,  # will tick 3 → 4 → +2
        "source_runs": ["old"], "total_occurrences": 10,
    }]
    merge.merge(profile, detections=[], run_id="r-d", now=_now())

    assert profile["milestone_xp"] == 2  # streak hit 4 → +2
    assert profile["session_banked_xp"] == 0
    data = json.loads(merge.STREAK_REWARD_MARKER.read_text())
    assert data["rewards"][0]["direction"] == "negative"
    assert data["rewards"][0]["streak"] == 4
    assert data["rewards"][0]["xp_awarded"] == 2


# --- reward_hint precedence through merge ----------------------------------

def test_explicit_reward_hint_preserved(_marker_cleanup):
    """Detection with explicit reward_hint beats keyword inference."""
    profile = _blank_profile()
    detections = [{
        "id": "edits-without-testing",   # would infer test_run via keyword
        "name": "edits without testing",
        "direction": "negative",
        "priority": 4,
        "nudge": "skipped tests after edits",
        "reward_hint": {"action": "commit", "xp": 1, "description": "custom"},
    }]
    merge.merge(profile, detections, run_id="r1", now=_now())

    entry = profile["entries"][0]
    assert entry["reward_hint"]["action"] == "commit"
    assert entry["reward_hint"]["description"] == "custom"


def test_inference_backfills_when_no_explicit_hint(_marker_cleanup):
    """Detection without reward_hint + nudge has keyword → inference fills it."""
    profile = _blank_profile()
    detections = [{
        "id": "edits-without-testing",
        "name": "edits without testing",
        "direction": "negative",
        "priority": 4,
        "nudge": "skipped tests after edits",
    }]
    merge.merge(profile, detections, run_id="r1", now=_now())

    entry = profile["entries"][0]
    assert entry["reward_hint"] is not None
    assert entry["reward_hint"]["action"] == "test_run"


# --- skills_by_project accumulator -----------------------------------------

def test_merge_skills_by_project_sums_across_projects():
    existing = {"service": {"deploy-staging": 3, "design": 1}}
    delta    = {"service": {"deploy-staging": 2}, "widget": {"widget-build": 1}}
    out = merge.merge_skills_by_project(existing, delta)
    assert out["service"]["deploy-staging"] == 5
    assert out["service"]["design"] == 1
    assert out["widget"]["widget-build"] == 1


def test_merge_skills_by_project_returns_new_dict():
    """Must not mutate caller's data — merge_skills_by_project is called
    inside the locked profile-write path; aliasing the existing dict
    would let a partial write leak across runs if anything failed
    mid-process."""
    existing = {"service": {"deploy-staging": 1}}
    delta    = {"service": {"deploy-staging": 1}}
    out = merge.merge_skills_by_project(existing, delta)
    assert existing["service"]["deploy-staging"] == 1   # untouched
    assert out["service"]["deploy-staging"] == 2


def test_merge_skills_by_project_drops_garbage():
    """Hostile shapes (non-dict skills, non-numeric counts) must be
    silently filtered. The deterministic insights pass runs unattended
    on cron; a single bad cell elsewhere in the profile shouldn't block
    this merge."""
    existing = {"service": {"deploy-staging": "not-a-number"}, "broken": "string"}
    delta    = {"widget": {"widget-build": 2}, 12345: {"x": 1}}
    out = merge.merge_skills_by_project(existing, delta)
    assert "broken" not in out
    assert out["service"] == {}                          # bad value dropped
    assert out["widget"]["widget-build"] == 2
    # Non-string project keys get coerced rather than crashing.
    assert "12345" in out


def test_merge_skills_by_project_handles_empty_inputs():
    assert merge.merge_skills_by_project({}, {}) == {}
    assert merge.merge_skills_by_project(None, None) == {}
    assert merge.merge_skills_by_project({"a": {"x": 1}}, {}) == {"a": {"x": 1}}
    assert merge.merge_skills_by_project({}, {"a": {"x": 1}}) == {"a": {"x": 1}}


# --- merge.main() end-to-end (argparse → flock → atomic write) -------------
#
# These exercise the full CLI wire-up that production /coach-insights uses, not
# just the merge_skills_by_project helper. Regression for review-finding #2
# (2026-04-24): a rename of the --skills-by-project-delta flag, or a
# forgotten persist after merge_skills_by_project, would pass every prior
# test in this file but break production. These guard the wire-up.


def _run_merge_main(monkeypatch, **paths):
    """Invoke merge.main() with the given file paths via sys.argv,
    matching how insights.sh invokes it. Returns merge.main()'s exit
    code so tests can assert on success/failure."""
    argv = ["merge.py", "--run-id", "r-test"]
    for flag, p in paths.items():
        argv.extend([f"--{flag.replace('_', '-')}", str(p)])
    monkeypatch.setattr("sys.argv", argv)
    return merge.main()


def test_main_persists_skills_by_project_delta_into_profile(
        tmp_path, monkeypatch):
    """Drive merge.main() end-to-end with a delta and verify the
    rolling accumulator lands in the written profile. This is the
    test the review-audit flagged as missing — without it, a refactor
    that broke the persist step would still see all 53 prior tests
    pass."""
    profile_path = tmp_path / "profile.yaml"
    yaml.safe_dump(_blank_profile(), profile_path.open("w"))

    detections = tmp_path / "det.json"
    detections.write_text("[]")

    delta = tmp_path / "delta.json"
    delta.write_text(json.dumps(
        {"service": {"deploy-staging": 3}, "widget": {"design": 2}}))

    rc = _run_merge_main(
        monkeypatch,
        profile=profile_path,
        changelog=tmp_path / "changelog.md",
        lock=tmp_path / ".lock",
        detections=detections,
        skills_by_project_delta=delta,
    )
    assert rc in (0, None)   # main() returns None on success path

    written = yaml.safe_load(profile_path.read_text())
    assert written["skills_by_project"] == {
        "service":    {"deploy-staging": 3},
        "widget": {"design": 2},
    }


def test_main_accumulates_delta_into_existing_skills_by_project(
        tmp_path, monkeypatch):
    """A second run on top of an existing accumulator must SUM, not
    replace. Locks the accumulator semantics at the CLI boundary."""
    profile_path = tmp_path / "profile.yaml"
    profile = _blank_profile()
    profile["skills_by_project"] = {"service": {"deploy-staging": 5}}
    yaml.safe_dump(profile, profile_path.open("w"))

    detections = tmp_path / "det.json"
    detections.write_text("[]")

    delta = tmp_path / "delta.json"
    delta.write_text(json.dumps({"service": {"deploy-staging": 2}}))

    _run_merge_main(
        monkeypatch,
        profile=profile_path,
        changelog=tmp_path / "changelog.md",
        lock=tmp_path / ".lock",
        detections=detections,
        skills_by_project_delta=delta,
    )

    written = yaml.safe_load(profile_path.read_text())
    assert written["skills_by_project"]["service"]["deploy-staging"] == 7


def test_main_preserves_skills_by_project_when_no_delta_passed(
        tmp_path, monkeypatch):
    """A run without --skills-by-project-delta must NOT clobber the
    existing rolling counter. Guards a future refactor that defaults
    the field to {} on every run (the `if sbp_delta:` guard at the
    relevant line in main() would silently break otherwise)."""
    profile_path = tmp_path / "profile.yaml"
    profile = _blank_profile()
    profile["skills_by_project"] = {"service": {"deploy-staging": 5}}
    yaml.safe_dump(profile, profile_path.open("w"))

    detections = tmp_path / "det.json"
    detections.write_text("[]")

    _run_merge_main(
        monkeypatch,
        profile=profile_path,
        changelog=tmp_path / "changelog.md",
        lock=tmp_path / ".lock",
        detections=detections,
        # NO skills_by_project_delta on purpose
    )

    written = yaml.safe_load(profile_path.read_text())
    assert written["skills_by_project"] == {"service": {"deploy-staging": 5}}


def test_main_emits_changelog_fragment_for_new_pairs(
        tmp_path, monkeypatch):
    """Regression for the `+sbp:N` changelog fragment counting logic.
    Currently it counts only NEW (project, skill) pairs, not increments
    on existing ones. This test locks that behavior so a future change
    has to be deliberate."""
    profile_path = tmp_path / "profile.yaml"
    profile = _blank_profile()
    profile["skills_by_project"] = {"service": {"deploy-staging": 5}}
    yaml.safe_dump(profile, profile_path.open("w"))

    detections = tmp_path / "det.json"
    detections.write_text("[]")

    delta = tmp_path / "delta.json"
    # One existing pair (deploy-staging@service, just incrementing) plus
    # one new pair (widget-build@widget, never seen). Expect +sbp:1.
    delta.write_text(json.dumps({
        "service":    {"deploy-staging": 1},
        "widget": {"widget-build": 4},
    }))

    changelog = tmp_path / "changelog.md"
    _run_merge_main(
        monkeypatch,
        profile=profile_path,
        changelog=changelog,
        lock=tmp_path / ".lock",
        detections=detections,
        skills_by_project_delta=delta,
    )

    line = changelog.read_text()
    assert "+sbp:1" in line, (
        f"expected `+sbp:1` for the one new (project,skill) pair; "
        f"changelog was: {line!r}"
    )
