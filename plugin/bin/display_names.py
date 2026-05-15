"""Slug → user-facing display name mapping.

Single source of truth used by every banner, ack, and /coach status row.
Resolution order (canonical, `positive_frame=False`):
  1. WORDING_OVERRIDES — curated wording for known patterns
  2. profile["entries"|"graduated"|"archived"|"strengths"] entry.name
     (skipped when name == id, defending against the analyze.py:295
     class of bug where slug accidentally lands in the name field)
  3. Humanized slug — `entry_id.replace("-", " ")`
  4. The entry_id itself (defensive — empty string stays empty)

Resolution order (positive-inverse, `positive_frame=True`):
  1. INVERSE_OVERRIDES — curated positive inverse for negative slugs
  2. On miss: `"avoiding {canonical}"` where `{canonical}` runs the
     full chain above (WORDING_OVERRIDES → profile entry.name →
     humanized slug). The wrapper is load-bearing for slugs from
     `aggregate_facets.py` (open-ended `friction_counts.*` keys
     from Anthropic's facet data) and any future analyzer detector
     that ships before its curator gets to the table. Without it,
     an uncurated negative slug would render as
     `↑ misunderstood request +1` — the v1.0.10 cue conflict.
This is used by EARNING surfaces (mid-streak ticks, negative-direction
graduations, weakness tip-completion acks) where the user just took the
positive action and should see it named. SLIPPING surfaces (regression
banner) and OPERATOR/MODEL surfaces (/coach status, SessionStart
watchlist, tip generator prompt) always use the canonical resolution.
"""
from __future__ import annotations


WORDING_OVERRIDES: dict[str, str] = {
    # Weakness patterns
    "edits-without-testing":       "edits without testing",
    "commit-without-testing":      "committing without testing",
    "under-planning":              "thin planning",
    "skipped-search-tools":        "skipping search tools",
    "exploration-without-landing": "exploration without landing",
    "heavy-agent-delegation":      "heavy subagent delegation",
    "heavy-subagent-delegation":   "heavy subagent delegation",
    "buggy-code":                  "buggy code",
    "wrong-approach":              "wrong approach",
    # Strength patterns
    "tests-after-edits":           "testing after edits",
    "safe-git-hygiene":            "safe git hygiene",
    "effective-skill-use":         "effective skill use",
}

INVERSE_OVERRIDES: dict[str, str] = {
    "commit-without-testing":      "testing before committing",
    "edits-without-testing":       "testing during edits",
    "under-planning":              "thorough planning",
    "skipped-search-tools":        "using search tools",
    "exploration-without-landing": "focused exploration",
    "heavy-agent-delegation":      "right-sized delegation",
    "heavy-subagent-delegation":   "right-sized delegation",
    "buggy-code":                  "avoiding buggy code",
    "wrong-approach":              "choosing the right approach",
}

_PROFILE_BUCKETS = ("entries", "graduated", "archived", "strengths")


def display_name(
    entry_id: str,
    profile: dict | None = None,
    *,
    positive_frame: bool = False,
) -> str:
    """Resolve `entry_id` to a user-facing display name.

    When `positive_frame=True`, INVERSE_OVERRIDES is consulted first;
    on miss, the canonical name is wrapped in an "avoiding {name}"
    fallback. This is load-bearing because the analyzer is not the
    only source of negative slugs — `aggregate_facets.py` emits
    arbitrary kebab-case slugs from Anthropic's `friction_counts.*`
    facet keys, which we cannot curate ahead of time. Without the
    fallback, an uncurated negative slug renders as
    `↑ misunderstood request +1` on the earning surface — the exact
    cue conflict v1.0.10 set out to eliminate. CALLER CONTRACT:
    positive_frame=True must only be passed for direction:negative
    entries; passing it for a strength yields "avoiding <strength>"
    which is a caller bug, not a renderer bug.

    When `positive_frame=False` (default), the canonical chain runs:
    WORDING_OVERRIDES → profile entry.name → humanized slug. Used by
    slipping surfaces (regression), operator views (/coach status),
    and LLM diagnostic context (SessionStart watchlist, tip prompt).

    The `name == id` guard catches the analyze.py bug class where a
    detection accidentally writes its slug into the `name` field; in
    that case we fall through to humanized-slug rendering rather than
    leaking the kebab-case form.
    """
    if not entry_id:
        return entry_id
    if positive_frame and entry_id in INVERSE_OVERRIDES:
        return INVERSE_OVERRIDES[entry_id]
    canonical = _resolve_canonical(entry_id, profile)
    if positive_frame:
        # Uncurated negative slug on an earning surface — prepend
        # "avoiding" so the row never reads as "the bad thing
        # increased." Safe for any kebab-case negative slug from
        # analyze.py OR aggregate_facets.py.
        return f"avoiding {canonical}"
    return canonical


def _resolve_canonical(entry_id: str, profile: dict | None) -> str:
    """Canonical-chain resolution: WORDING_OVERRIDES → profile entry.name
    → humanized slug. Factored out so display_name() can apply the
    positive-frame "avoiding {x}" wrapper without re-implementing the
    chain."""
    if entry_id in WORDING_OVERRIDES:
        return WORDING_OVERRIDES[entry_id]
    if isinstance(profile, dict):
        for bucket in _PROFILE_BUCKETS:
            for entry in profile.get(bucket) or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("id") == entry_id:
                    name = entry.get("name")
                    if name and name != entry_id:
                        return name
                    break
    return entry_id.replace("-", " ")
