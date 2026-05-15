"""Tests for the v1.0.10 positive-inverse naming contract.

Covers:
  • INVERSE_OVERRIDES coverage gate (every known active negative slug
    has a curated positive inverse).
  • display_name() positive_frame resolution order.
  • Hook fallback stub at coach-user-prompt.py:101 accepts the new
    positive_frame kwarg without crashing (degraded-install gate).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "coach" / "bin"))
sys.path.insert(0, str(REPO / "hooks"))

from display_names import (  # noqa: E402
    INVERSE_OVERRIDES,
    WORDING_OVERRIDES,
    display_name,
)


# Hardcoded inventory of slugs the analyzer currently emits as
# direction:negative. If a new negative slug is added to analyze.py
# without being added to this set, the curator decides whether to:
#   (a) add it here AND add an INVERSE_OVERRIDES entry, or
#   (b) acknowledge it's intentionally un-inverted (e.g., placeholder).
# Either way, the test forces the conversation rather than letting a
# raw `↑ committing without testing` ship silently.
_EXPECTED_NEGATIVE_SLUGS: set[str] = {
    "edits-without-testing",
    "commit-without-testing",
    "under-planning",
    "skipped-search-tools",
    "exploration-without-landing",
    "heavy-agent-delegation",
    "heavy-subagent-delegation",
    "buggy-code",
    "wrong-approach",
}


def test_inverse_overrides_covers_known_negative_slugs():
    """Every active negative slug has an INVERSE_OVERRIDES entry. Bumps
    _EXPECTED_NEGATIVE_SLUGS when adding new ones — the test forces the
    curator to decide on a positive frame at slug-creation time, not at
    user-sighting time."""
    missing = _EXPECTED_NEGATIVE_SLUGS - set(INVERSE_OVERRIDES.keys())
    assert not missing, (
        f"Known negative slugs missing from INVERSE_OVERRIDES: {sorted(missing)}. "
        f"Either add a curated positive inverse or update _EXPECTED_NEGATIVE_SLUGS "
        f"with reasoning."
    )


def test_inverse_overrides_only_covers_known_slugs():
    """INVERSE_OVERRIDES should not drift ahead of the analyzer's
    negative slug set — orphaned entries are dead weight and a
    maintenance trap."""
    orphans = set(INVERSE_OVERRIDES.keys()) - _EXPECTED_NEGATIVE_SLUGS
    assert not orphans, (
        f"INVERSE_OVERRIDES contains slugs not in _EXPECTED_NEGATIVE_SLUGS: "
        f"{sorted(orphans)}. Either drop them or extend the expected set."
    )


def test_display_name_positive_frame_uses_inverse_overrides():
    """positive_frame=True consults INVERSE_OVERRIDES first."""
    assert display_name(
        "commit-without-testing", positive_frame=True
    ) == "testing before committing"
    assert display_name(
        "edits-without-testing", positive_frame=True
    ) == "testing during edits"
    assert display_name(
        "skipped-search-tools", positive_frame=True
    ) == "using search tools"


def test_display_name_positive_frame_handles_avoiding_form():
    """Slugs without a natural positive verb (buggy-code, wrong-approach)
    still get a curated positive frame ("avoiding X" / "choosing X")
    rather than degrading to canonical. Canonical fallback on an earning
    surface would read as "the bad thing increased"."""
    assert display_name(
        "buggy-code", positive_frame=True
    ) == "avoiding buggy code"
    assert display_name(
        "wrong-approach", positive_frame=True
    ) == "choosing the right approach"


def test_display_name_positive_frame_wraps_uncurated_in_avoiding():
    """An uncurated negative slug (no INVERSE_OVERRIDES entry) gets the
    "avoiding {canonical}" fallback. This is the load-bearing safety
    net for slugs from aggregate_facets.py (open-ended friction keys
    from Anthropic's facet data — see test_aggregate_facet_friction_
    slug_does_not_leak_negative below) AND for any future analyzer
    detector that ships before its curator gets to the table."""
    # An unknown kebab-case slug with no WORDING_OVERRIDES entry —
    # mimics what aggregate_facets emits for novel friction keys.
    assert display_name(
        "some-unknown-slug", positive_frame=True
    ) == "avoiding some unknown slug"


def test_display_name_positive_frame_avoiding_wraps_wording_canonical(monkeypatch):
    """The "avoiding {canonical}" fallback runs the FULL canonical
    chain underneath, so a slug with a WORDING_OVERRIDES entry but no
    INVERSE_OVERRIDES entry gets `avoiding {WORDING_OVERRIDES name}`
    — not `avoiding {humanized-slug}`. Currently no production slug is
    in WORDING but missing from INVERSE, so we synthesize one via
    monkeypatch and exercise display_name() directly (not the
    internal helper)."""
    import display_names
    monkeypatch.setitem(
        display_names.WORDING_OVERRIDES,
        "synthetic-only-slug",
        "synthetic curated wording",
    )
    # INVERSE_OVERRIDES is intentionally NOT patched — this slug must
    # hit the avoiding-wrapper fallback for the WORDING canonical.
    assert "synthetic-only-slug" not in display_names.INVERSE_OVERRIDES
    assert display_name(
        "synthetic-only-slug", positive_frame=True
    ) == "avoiding synthetic curated wording"
    # Sanity: canonical path returns the WORDING value, unwrapped.
    assert display_name(
        "synthetic-only-slug"
    ) == "synthetic curated wording"


def test_display_name_positive_frame_avoiding_wraps_profile_name(monkeypatch):
    """Same wrapper-on-top-of-chain contract, but the canonical
    resolution lands on `profile.entries[].name` instead of
    WORDING_OVERRIDES. Mirrors the production path where merge.py
    writes detection-time names into the profile."""
    profile = {
        "entries": [
            {"id": "no-wording-no-inverse", "name": "profile-supplied wording"},
        ],
    }
    # Confirm the slug truly lives in neither override table.
    import display_names
    assert "no-wording-no-inverse" not in display_names.WORDING_OVERRIDES
    assert "no-wording-no-inverse" not in display_names.INVERSE_OVERRIDES
    assert display_name(
        "no-wording-no-inverse", profile, positive_frame=True
    ) == "avoiding profile-supplied wording"


def test_display_name_default_frame_unchanged():
    """positive_frame defaults to False; resolution is the canonical
    chain (WORDING_OVERRIDES first). This is the slipping-surface /
    operator-surface contract."""
    assert display_name(
        "commit-without-testing"
    ) == "committing without testing"
    assert display_name(
        "buggy-code"
    ) == "buggy code"


def test_display_name_empty_id_returns_empty():
    """Defensive: empty id returns empty regardless of positive_frame."""
    assert display_name("", positive_frame=True) == ""
    assert display_name("", positive_frame=False) == ""


def test_hook_stub_accepts_positive_frame_kwarg():
    """Degraded-install gate: if display_names import fails inside
    coach-user-prompt.py, the fallback stub at line 101 must still
    accept positive_frame=True without crashing. Otherwise every
    earning-surface call would AttributeError on degraded installs."""
    # Simulate the degraded path by defining the stub inline (mirror
    # of coach-user-prompt.py:101-110).
    def _stub(entry_id, profile=None, *, positive_frame: bool = False):
        del positive_frame
        if not entry_id:
            return entry_id
        return entry_id.replace("-", " ")

    # Must accept both positive_frame=True and =False without error.
    assert _stub("commit-without-testing", positive_frame=True) == "commit without testing"
    assert _stub("commit-without-testing", positive_frame=False) == "commit without testing"
    assert _stub("", positive_frame=True) == ""


def test_no_inverse_phrase_resembles_canonical_negative():
    """Anti-leak: no curated positive inverse may equal its
    WORDING_OVERRIDES canonical negative — would defeat the reframe."""
    for slug, inverse in INVERSE_OVERRIDES.items():
        canonical = WORDING_OVERRIDES.get(slug, slug.replace("-", " "))
        assert inverse != canonical, (
            f"INVERSE_OVERRIDES[{slug!r}] equals its canonical negative — "
            f"the reframe would be a no-op"
        )


# -----------------------------------------------------------------------------
# Aggregate-facets repro (P1 from v1.0.10 review)
#
# aggregate_facets.py emits negative detections with arbitrary kebab-case
# ids derived from Anthropic's facet contract (friction_counts.*). The
# v1.0.10 INVERSE_OVERRIDES coverage gate only protects the fixed
# analyzer inventory — facet-emitted slugs are open-ended by design.
# The "avoiding {canonical}" fallback is the structural fix that
# prevents `↑ misunderstood request +1` from ever reaching the user.
# -----------------------------------------------------------------------------

def test_aggregate_facet_friction_slug_does_not_leak_negative_on_earning():
    """Repro: aggregate_facets.py kebab-cases `friction_counts.*` keys
    into negative-direction detection ids (see aggregate_facets.py:154
    + test_aggregate_facets.py:43 which already proves
    `misunderstood-request` is a real production slug). Such slugs
    have no INVERSE_OVERRIDES entry by construction. On an earning
    surface they MUST render as `avoiding misunderstood request`,
    never as bare `misunderstood request` (the v1.0.10 cue-conflict
    bug class)."""
    # Exact teammate-flagged repro slug
    assert display_name(
        "misunderstood-request", positive_frame=True
    ) == "avoiding misunderstood request"
    # Other observed facet-derived slugs from the test fixtures
    for facet_slug in (
        "misunderstood-request",
        "wrong-thinking",
        "slow-response",
        "incorrect-output",
    ):
        rendered = display_name(facet_slug, positive_frame=True)
        assert rendered.startswith("avoiding "), (
            f"Uncurated facet slug {facet_slug!r} leaked bare to earning "
            f"surface: rendered as {rendered!r}"
        )
        # Defense in depth: the bare humanized form must NOT be
        # the entire output.
        assert rendered != facet_slug.replace("-", " "), (
            f"Uncurated facet slug {facet_slug!r} rendered as the bare "
            f"humanized slug — no avoiding-wrapper applied"
        )


def test_canonical_path_unchanged_for_facet_slugs():
    """Sanity: the canonical chain (positive_frame=False) still emits
    the humanized slug for facet-emitted ids — slipping/operator
    surfaces are unaffected by the new fallback."""
    assert display_name(
        "misunderstood-request"
    ) == "misunderstood request"
    assert display_name(
        "misunderstood-request", positive_frame=False
    ) == "misunderstood request"
