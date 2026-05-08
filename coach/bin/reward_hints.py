"""
Shared reward-hint inference for the Coach system.

A `reward_hint` attached to a profile.yaml entry specifies what user action
completes a tip for that pattern, and how much XP each action earns. Shape:

    reward_hint:
      action: test_run | commit | skill_invoke   # named action
      xp: 2                                      # per-action XP
      description: "test run (pytest / ...)"      # human-readable for tip

This module infers reasonable defaults from an entry's id + nudge text when
no explicit hint is set. It's imported by:

  - coach/bin/merge.py              — to populate reward_hint at entry creation
  - hooks/coach-user-prompt.py      — as read-time fallback for existing entries

Single source of truth for the keyword heuristic + detector vocabulary.
Hooks can't simply `import reward_hints` because they run from ~/.claude/
with a different cwd; they must append coach/bin/ to sys.path first.
"""
from __future__ import annotations

# (keyword, reward_hint payload). First match wins. Keywords are case-
# insensitive and checked against BOTH the entry id and the nudge text,
# so a pattern with id="over-mocks" whose nudge says "wrote code without
# running tests" still trips test_run via the nudge.
_HEURISTIC: list[tuple[str, dict]] = [
    # Test-run signals (broadest coverage: id-tokens + common nudge phrasings)
    ("without-test",     {"action": "test_run", "xp": 2,
                          "description": "test run (pytest / jest / cargo test / …)"}),
    ("without test",     {"action": "test_run", "xp": 2, "description": "test run"}),
    ("no test",          {"action": "test_run", "xp": 2, "description": "test run"}),
    ("no tests",         {"action": "test_run", "xp": 2, "description": "test run"}),
    ("untested",         {"action": "test_run", "xp": 2, "description": "test run"}),
    ("skip-test",        {"action": "test_run", "xp": 2, "description": "test run"}),
    ("skip test",        {"action": "test_run", "xp": 2, "description": "test run"}),
    ("skipped test",     {"action": "test_run", "xp": 2, "description": "test run"}),
    ("skipping test",    {"action": "test_run", "xp": 2, "description": "test run"}),
    ("tests skipped",    {"action": "test_run", "xp": 2, "description": "test run"}),
    ("tests were skipped", {"action": "test_run", "xp": 2, "description": "test run"}),
    ("running tests",    {"action": "test_run", "xp": 2, "description": "test run"}),
    ("run tests",        {"action": "test_run", "xp": 2, "description": "test run"}),
    ("test run",         {"action": "test_run", "xp": 2, "description": "test run"}),
    ("test suite",       {"action": "test_run", "xp": 2, "description": "test run"}),
    # Commit signals
    ("without-commit",     {"action": "commit", "xp": 1, "description": "git commit"}),
    ("without committing", {"action": "commit", "xp": 1, "description": "git commit"}),
    ("not committing",     {"action": "commit", "xp": 1, "description": "git commit"}),
]


def infer_reward_hint(entry: dict) -> dict | None:
    """Guess the reward_hint for a profile entry (or a detection dict) that
    doesn't have one set. Inspects both the id and the nudge text for
    keyword hits. Returns None when nothing matches → graduation-only pattern.

    Accepts either:
      - a profile.yaml entry dict (has `id`, `nudge`, ...)
      - an analyze.py detection dict (has `id`, `nudge`, ...)
    Both share the same relevant keys.
    """
    if not isinstance(entry, dict):
        return None
    eid = str(entry.get("id") or "").lower()
    nudge = str(entry.get("nudge") or "").lower()
    haystack = f"{eid} {nudge}"
    for keyword, hint in _HEURISTIC:
        if keyword in haystack:
            return dict(hint)  # copy so callers can't mutate our defaults
    return None


def effective_reward_hint(entry: dict) -> dict | None:
    """Return the entry's explicit reward_hint if present and valid, else
    infer. Centralizes the "explicit overrides inference" rule so every
    caller gets the same precedence."""
    if not isinstance(entry, dict):
        return None
    explicit = entry.get("reward_hint")
    if (
        isinstance(explicit, dict)
        and explicit.get("action")
        and int(explicit.get("xp", 0) or 0) > 0
    ):
        return explicit
    return infer_reward_hint(entry)
