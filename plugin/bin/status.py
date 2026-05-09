#!/usr/bin/env python3
"""
Comprehensive coach progress breakdown for `/coach status`.

Reports:
  - Current level + XP total + distance to next level
  - Lifetime XP breakdown: graduations, streak, session banking, milestones
  - Session XP breakdown: test runs, commits, skills (live from current transcript)
  - How to earn more (cheat sheet)
  - Profile state: probationary / active / graduated / archived patterns
  - Recent /coach-insights activity

Invoked by the /coach skill. Output is plain text with light ANSI color.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coach_paths import resolve_coach_dir  # noqa: E402

COACH_DIR = resolve_coach_dir()
PROFILE = COACH_DIR / "profile.yaml"
CHANGELOG = COACH_DIR / "changelog.md"
LEDGER = COACH_DIR / "banked_sessions.json"
PROJECTS = Path.home() / ".claude" / "projects"

# Ladder is imported from stats.py so `/coach status` and the statusline
# can never disagree on level name, threshold, or max rank. The legacy
# 8-level table used to be inlined here and capped at L8 Sensei, which
# reported "max level" for any lifetime XP > 90 even though stats.py
# ranked all the way to L50 Origin.
from stats import LEVELS as _STATS_LEVELS  # type: ignore  # noqa: E402
from scoring import score_transcript_with_breakdown  # type: ignore  # noqa: E402
from xp_accounting import normalize_profile_xp  # type: ignore  # noqa: E402
LEVELS = _STATS_LEVELS

SESSION_XP_CAP = 15
BAR_SEGMENTS = 10  # wider bar in /status than statusline
GRADUATION_STREAK_TARGET = 5  # matches coach-user-prompt.py; mid-streak path

BOLD  = "\x1b[1m"
DIM   = "\x1b[2m"
RESET = "\x1b[0m"
CYAN  = "\x1b[38;2;200;214;229m"
GREY  = "\x1b[38;2;110;122;140m"
GOLD  = "\x1b[38;2;212;175;55m"


def _level_for(xp: int):
    idx = 0
    for i, (thr, _) in enumerate(LEVELS):
        if xp >= thr:
            idx = i
    cur = LEVELS[idx][0]
    nxt = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None
    return idx, LEVELS[idx][1], cur, nxt


def _current_transcript() -> Path | None:
    """Find the most recently modified main transcript (likely current session)."""
    if not PROJECTS.exists():
        return None
    newest: Path | None = None
    newest_mtime = 0.0
    for p in PROJECTS.rglob("*.jsonl"):
        if "/subagents/" in str(p):
            continue
        try:
            m = p.stat().st_mtime
            if m > newest_mtime:
                newest_mtime = m
                newest = p
        except Exception:
            continue
    return newest


def _score(path: Path, profile: dict | None = None) -> dict:
    try:
        return score_transcript_with_breakdown(path, profile)
    except Exception:
        return {
            "tests": 0,
            "commits": 0,
            "skills_n": 0,
            "skills_list": [],
            "dynamic_actions": {},
            "available_dynamic_actions": {},
            "raw_xp": 0,
            "capped_xp": 0,
            "capped": False,
        }


def _bar(filled: int, total: int) -> str:
    return CYAN + "[" + ("▰" * filled) + RESET + GREY + ("▱" * (total - filled)) + RESET + CYAN + "]" + RESET


def _streak_bar(streak: int, target: int = GRADUATION_STREAK_TARGET) -> str:
    """🔴🔴🔴⚪⚪ bar — red fill for earned positions, hollow white for
    remaining. Matches coach-user-prompt.py:_streak_bar so /coach status
    and the in-chat tip share the same glyph + color story without
    needing ANSI in either surface."""
    streak = max(0, min(streak, target))
    return "🔴" * streak + "⚪" * (target - streak)


def main() -> int:
    if not PROFILE.exists():
        print("Coach profile not found. Run /coach-insights to initialize.")
        return 0

    try:
        import yaml
        profile = yaml.safe_load(PROFILE.read_text()) or {}
    except Exception as e:
        print(f"Failed to read profile.yaml: {e}")
        return 0

    entries = profile.get("entries", []) or []
    graduated = [g for g in (profile.get("graduated") or []) if isinstance(g, dict)]
    archived = [a for a in (profile.get("archived") or []) if isinstance(a, dict)]
    xp = normalize_profile_xp(profile)
    graduation_xp = int(xp["graduation_xp"])
    max_streak = int(xp["max_active_clean_streak"])
    session_banked_xp = int(xp["session_banked_xp"])
    milestone_xp = int(xp["milestone_xp"])
    manual_adjustments = int(xp["manual_adjustments"])
    lifetime_xp = int(xp["lifetime_xp"])

    tpath = _current_transcript()
    session = _score(tpath, profile) if tpath else {
        "tests": 0,
        "commits": 0,
        "skills_n": 0,
        "skills_list": [],
        "dynamic_actions": {},
        "available_dynamic_actions": {},
        "raw_xp": 0,
        "capped_xp": 0,
        "capped": False,
    }
    session_xp = session["capped_xp"]

    total = lifetime_xp + session_xp
    idx, name, cur, nxt = _level_for(total)
    if nxt is None:
        progress_label = "max level"
        filled = BAR_SEGMENTS
        to_next = 0
    else:
        span = max(1, nxt - cur)
        prog = max(0.0, min(1.0, (total - cur) / span))
        filled = int(round(prog * BAR_SEGMENTS))
        if filled == BAR_SEGMENTS and total < nxt:
            filled = BAR_SEGMENTS - 1
        # "Just arrived" bump — first segment lit as soon as they've earned
        # their way past Drafter, so a fresh level-up never reads as empty.
        if filled == 0 and idx > 0:
            filled = 1
        to_next = nxt - total
        progress_label = f"{to_next} xp to {LEVELS[idx + 1][1]}"

    # --- Header ---
    print(f"{BOLD}{GOLD}L{idx + 1} {name}{RESET}  {_bar(filled, BAR_SEGMENTS)}  {CYAN}{total} xp{RESET}  {GREY}· {progress_label}{RESET}")
    print()

    # --- Lifetime breakdown ---
    print(f"{BOLD}Lifetime ({lifetime_xp} xp){RESET}")
    print(f"  {graduation_xp:3d} xp  {GREY}·{RESET} graduated patterns ({len(graduated)} × 5)")
    print(f"  {max_streak:3d} xp  {GREY}·{RESET} longest active clean streak")
    n_banked = len(json.loads(LEDGER.read_text())) if LEDGER.exists() else 0
    print(f"  {session_banked_xp:3d} xp  {GREY}·{RESET} completed sessions ({n_banked} sessions at 10:1)")
    print(f"  {milestone_xp:3d} xp  {GREY}·{RESET} mid-streak milestones")
    if manual_adjustments:
        print(f"  {manual_adjustments:3d} xp  {GREY}·{RESET} manual adjustments")
    print()

    # --- Session breakdown ---
    label_session = f"Session ({session_xp} xp"
    if session["capped"]:
        label_session += f", capped from {session['raw_xp']}"
    label_session += ", cap 15)"
    print(f"{BOLD}{label_session}{RESET}")
    print(f"  {session['tests'] * 2:3d} xp  {GREY}·{RESET} test runs ({session['tests']} × 2)")
    print(f"  {session['commits']:3d} xp  {GREY}·{RESET} git commits ({session['commits']})")
    skills_preview = ", ".join(session["skills_list"][:5])
    if len(session["skills_list"]) > 5:
        skills_preview += f", +{len(session['skills_list']) - 5} more"
    print(f"  {session['skills_n']:3d} xp  {GREY}·{RESET} unique skills invoked ({skills_preview or 'none'})")
    for action, info in (session.get("dynamic_actions") or {}).items():
        count = int(info.get("count", 0) or 0)
        xp_each = int(info.get("xp_each", 0) or 0)
        xp = int(info.get("xp", 0) or 0)
        print(f"  {xp:3d} xp  {GREY}·{RESET} {action} ({count} × {xp_each})")
    print()

    # --- How to earn more ---
    print(f"{BOLD}How to earn more{RESET}")
    print(f"  {GOLD}+2{RESET} per test runner (pytest / jest / cargo test / ...)")
    print(f"  {GOLD}+1{RESET} per git commit")
    print(f"  {GOLD}+1{RESET} per unique skill invoked (this session)")
    available_dynamic_actions = session.get("available_dynamic_actions") or {}
    for action, xp_each in available_dynamic_actions.items():
        xp_each = int(xp_each or 0)
        print(f"  {GOLD}+{xp_each}{RESET} per {action} action from active reward hints")
    print(f"  {GOLD}+5{RESET} per graduated pattern (requires 5-run clean streak)")
    print(f"  {GREY}session xp banks at 10:1 into lifetime between sessions{RESET}")
    print()

    # --- Profile state ---
    weaknesses = [e for e in entries if isinstance(e, dict) and e.get("direction", "negative") == "negative"]
    strengths = [e for e in entries if isinstance(e, dict) and e.get("direction") == "positive"]
    grad_neg = [g for g in graduated if isinstance(g, dict) and g.get("direction", "negative") == "negative"]
    grad_pos = [g for g in graduated if isinstance(g, dict) and g.get("direction") == "positive"]
    archived_neg = [
        a for a in archived
        if isinstance(a, dict) and a.get("direction", "negative") == "negative"
    ]

    # Sort entries by streak descending within each section so the ones
    # closest to graduation/mastery read first.
    print(f"{BOLD}Weaknesses{RESET}")
    if weaknesses:
        actives_n = sum(1 for e in weaknesses if e.get("tier") == "active")
        probs_n = sum(1 for e in weaknesses if e.get("tier") == "probationary")
        print(
            f"  {actives_n} active  ·  {probs_n} probationary  ·  "
            f"{len(grad_neg)} retired  ·  {len(archived_neg)} archived"
        )
        weaknesses_sorted = sorted(
            weaknesses,
            key=lambda e: int(e.get("clean_streak_runs", 0) or 0),
            reverse=True,
        )
        for e in weaknesses_sorted[:5]:
            eid = e.get("id", "?")
            streak = int(e.get("clean_streak_runs", 0) or 0)
            tier = e.get("tier", "?")
            bar = _streak_bar(streak)
            label = "graduates" if streak >= GRADUATION_STREAK_TARGET else "to graduation"
            print(
                f"    {GREY}·{RESET} {eid}  {bar} {CYAN}{streak}/{GRADUATION_STREAK_TARGET}{RESET} "
                f"{GREY}({tier} · {label}){RESET}"
            )
        if len(weaknesses) > 5:
            print(f"    {GREY}… {len(weaknesses) - 5} more (see ~/.claude/coach/profile.yaml){RESET}")
    else:
        print(f"  {GREY}none tracked  ·  {len(archived_neg)} archived{RESET}")
    print()

    print(f"{BOLD}Strengths{RESET}")
    if strengths or grad_pos:
        actives_n = sum(1 for e in strengths if e.get("tier") == "active")
        probs_n = sum(1 for e in strengths if e.get("tier") == "probationary")
        print(f"  {actives_n} active  ·  {probs_n} probationary  ·  {len(grad_pos)} mastered")
        strengths_sorted = sorted(
            strengths,
            key=lambda e: int(e.get("positive_run_streak", 0) or 0),
            reverse=True,
        )
        for e in strengths_sorted[:5]:
            eid = e.get("id", "?")
            streak = int(e.get("positive_run_streak", 0) or 0)
            tier = e.get("tier", "?")
            bar = _streak_bar(streak)
            label = "masters" if streak >= GRADUATION_STREAK_TARGET else "to mastery"
            print(
                f"    {GREY}·{RESET} {eid}  {bar} {CYAN}{streak}/{GRADUATION_STREAK_TARGET}{RESET} "
                f"{GREY}({tier} · {label}){RESET}"
            )
        if len(strengths) > 5:
            print(f"    {GREY}… {len(strengths) - 5} more (see ~/.claude/coach/profile.yaml){RESET}")
    else:
        print(f"  {GREY}none tracked yet — /coach-insights scans for strengths at ≥60% session frequency{RESET}")

    # --- Latest /coach-insights ---
    if CHANGELOG.exists():
        try:
            last = [ln for ln in CHANGELOG.read_text().splitlines() if ln.strip()][-1]
            print(f"  {GREY}Last /coach-insights: {last[:120]}{RESET}")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
