"""Shared lifetime XP accounting for Coach profile data.

The profile used to store all non-graduation lifetime XP in
``banked_session_xp``. Newer code keeps the sources split so status output,
exports, and future UI can explain where progress came from:

  - session_banked_xp: completed-session XP converted at 10:1 by bank.py
  - milestone_xp: mid-streak rewards from merge.py
  - graduation_xp: derived from current graduated entries
  - manual_adjustments: explicit operator edits

``banked_session_xp`` remains as a deprecated alias for session banking only.
"""
from __future__ import annotations

GRADUATION_XP = 5


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def graduation_xp(profile: dict) -> int:
    graduated = [
        g for g in (profile.get("graduated") or [])
        if isinstance(g, dict)
    ]
    return len(graduated) * GRADUATION_XP


def max_active_clean_streak(profile: dict) -> int:
    max_streak = 0
    for entry in profile.get("entries", []) or []:
        if not isinstance(entry, dict):
            continue
        max_streak = max(max_streak, _as_int(entry.get("clean_streak_runs")))
    return max_streak


def normalize_profile_xp(profile: dict) -> dict:
    """Ensure split XP fields exist and return an explainable breakdown.

    Mutates ``profile`` in place. For legacy profiles with only
    ``banked_session_xp``, that value is migrated into ``session_banked_xp``.
    Historical milestone/session split cannot be recovered, so preserving the
    total is the safe migration.
    """
    if not isinstance(profile, dict):
        profile = {}

    has_split = any(
        key in profile
        for key in ("session_banked_xp", "milestone_xp", "manual_adjustments")
    )
    legacy_banked = _as_int(profile.get("banked_session_xp"))

    if has_split:
        session_banked = _as_int(profile.get("session_banked_xp"))
    else:
        session_banked = legacy_banked

    milestone = _as_int(profile.get("milestone_xp"))
    manual = _as_int(profile.get("manual_adjustments"))
    graduated = graduation_xp(profile)
    clean_streak = max_active_clean_streak(profile)

    profile["session_banked_xp"] = session_banked
    profile["milestone_xp"] = milestone
    profile["graduation_xp"] = graduated
    profile["manual_adjustments"] = manual
    # Deprecated compatibility alias. New code should read session_banked_xp.
    profile["banked_session_xp"] = session_banked

    lifetime = session_banked + milestone + graduated + clean_streak + manual
    return {
        "session_banked_xp": session_banked,
        "milestone_xp": milestone,
        "graduation_xp": graduated,
        "manual_adjustments": manual,
        "max_active_clean_streak": clean_streak,
        "lifetime_xp": lifetime,
    }


def add_session_banked_xp(profile: dict, amount: int) -> dict:
    normalize_profile_xp(profile)
    profile["session_banked_xp"] = _as_int(profile.get("session_banked_xp")) + _as_int(amount)
    profile["banked_session_xp"] = profile["session_banked_xp"]
    return normalize_profile_xp(profile)


def add_milestone_xp(profile: dict, amount: int) -> dict:
    normalize_profile_xp(profile)
    profile["milestone_xp"] = _as_int(profile.get("milestone_xp")) + _as_int(amount)
    return normalize_profile_xp(profile)
