"""Slug → display name resolution.

Pins the curated overrides, profile-name lookup, name-equals-id guard,
and humanized-slug fallback. The helper is the single source of truth
for every user-facing surface that mentions a pattern by name.
"""
from __future__ import annotations

import pytest

from display_names import WORDING_OVERRIDES, display_name


# -----------------------------------------------------------------------------
# Curated overrides — all 12 slugs round-trip
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(("entry_id", "expected"), list(WORDING_OVERRIDES.items()))
def test_curated_override_takes_precedence(entry_id, expected):
    assert display_name(entry_id) == expected


def test_curated_override_wins_over_profile_name():
    """Override table is the highest-priority source — even a profile
    entry with a populated `name` doesn't override the curated phrase."""
    profile = {"entries": [{"id": "under-planning", "name": "something else"}]}
    assert display_name("under-planning", profile) == "thin planning"


# -----------------------------------------------------------------------------
# Profile-name lookup (used when no override exists)
# -----------------------------------------------------------------------------

def test_profile_name_used_when_no_override():
    profile = {
        "entries": [{"id": "novel-pattern", "name": "novel pattern detected"}],
    }
    assert display_name("novel-pattern", profile) == "novel pattern detected"


def test_profile_lookup_searches_all_buckets():
    """name lookup must search graduated/archived/strengths too — entries
    can move between buckets across runs."""
    grad = {"graduated": [{"id": "x-y", "name": "X to Y"}]}
    arc  = {"archived":  [{"id": "x-y", "name": "X to Y"}]}
    st   = {"strengths": [{"id": "x-y", "name": "X to Y"}]}
    for profile in (grad, arc, st):
        assert display_name("x-y", profile) == "X to Y"


# -----------------------------------------------------------------------------
# `name == id` guard — defends against analyze.py:295 bug class
# -----------------------------------------------------------------------------

def test_name_equals_id_guard_falls_through_to_humanized():
    """If a detection wrote slug into name (the analyze.py:295 bug),
    we ignore it and render the humanized form."""
    profile = {"entries": [{"id": "broken-entry", "name": "broken-entry"}]}
    assert display_name("broken-entry", profile) == "broken entry"


# -----------------------------------------------------------------------------
# Humanized slug fallback
# -----------------------------------------------------------------------------

def test_humanized_slug_fallback_no_profile():
    assert display_name("my-new-pattern") == "my new pattern"


def test_humanized_slug_fallback_no_match_in_profile():
    profile = {"entries": [{"id": "different-thing", "name": "different thing"}]}
    assert display_name("my-new-pattern", profile) == "my new pattern"


def test_single_word_id_passes_through_unchanged():
    assert display_name("debug") == "debug"


# -----------------------------------------------------------------------------
# Defensive cases
# -----------------------------------------------------------------------------

def test_empty_id_returns_empty():
    assert display_name("") == ""


def test_none_profile_handled():
    assert display_name("under-planning", None) == "thin planning"
    assert display_name("novel-thing", None) == "novel thing"


def test_malformed_profile_handled():
    """Malformed bucket entries (non-dicts, missing keys) don't crash."""
    profile = {
        "entries": [None, "string-entry", {"no_id": "field"}, {"id": None}],
        "graduated": "not-a-list",
    }
    assert display_name("missing", profile) == "missing"
    assert display_name("missing-thing", profile) == "missing thing"


def test_profile_entry_with_no_name_falls_through():
    profile = {"entries": [{"id": "id-only"}]}
    assert display_name("id-only", profile) == "id only"
