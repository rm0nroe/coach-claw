"""Slug → user-facing display name mapping.

Single source of truth used by every banner, ack, and /coach status row.
Resolution order:
  1. WORDING_OVERRIDES — curated wording for known patterns
  2. profile["entries"|"graduated"|"archived"|"strengths"] entry.name
     (skipped when name == id, defending against the analyze.py:295
     class of bug where slug accidentally lands in the name field)
  3. Humanized slug — `entry_id.replace("-", " ")`
  4. The entry_id itself (defensive — empty string stays empty)
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

_PROFILE_BUCKETS = ("entries", "graduated", "archived", "strengths")


def display_name(entry_id: str, profile: dict | None = None) -> str:
    """Resolve `entry_id` to a user-facing display name.

    The `name == id` guard catches the analyze.py bug class where a
    detection accidentally writes its slug into the `name` field; in
    that case we fall through to humanized-slug rendering rather than
    leaking the kebab-case form.
    """
    if not entry_id:
        return entry_id
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
