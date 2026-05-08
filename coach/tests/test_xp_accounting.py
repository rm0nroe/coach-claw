from __future__ import annotations

from xp_accounting import (
    add_milestone_xp,
    add_session_banked_xp,
    normalize_profile_xp,
)


def test_legacy_banked_session_xp_migrates_to_session_bucket():
    profile = {
        "banked_session_xp": 7,
        "graduated": [{"id": "w1"}, {"id": "s1"}],
        "archived": [{"id": "aged-out"}],
        "entries": [{"id": "w2", "clean_streak_runs": 3}],
    }

    xp = normalize_profile_xp(profile)

    assert profile["session_banked_xp"] == 7
    assert profile["banked_session_xp"] == 7
    assert profile["milestone_xp"] == 0
    assert profile["graduation_xp"] == 10
    assert xp["lifetime_xp"] == 20


def test_split_fields_are_summed_without_refolding_milestones():
    profile = {
        "session_banked_xp": 4,
        "milestone_xp": 3,
        "manual_adjustments": -1,
        "banked_session_xp": 999,
        "graduated": [{"id": "w1"}],
        "entries": [{"id": "w2", "clean_streak_runs": 2}],
    }

    xp = normalize_profile_xp(profile)

    assert profile["session_banked_xp"] == 4
    assert profile["banked_session_xp"] == 4
    assert profile["milestone_xp"] == 3
    assert profile["graduation_xp"] == 5
    assert xp["lifetime_xp"] == 13


def test_add_helpers_write_the_correct_buckets():
    profile = {"graduated": [], "entries": []}

    add_session_banked_xp(profile, 2)
    add_milestone_xp(profile, 3)

    assert profile["session_banked_xp"] == 2
    assert profile["banked_session_xp"] == 2
    assert profile["milestone_xp"] == 3
    assert normalize_profile_xp(profile)["lifetime_xp"] == 5
