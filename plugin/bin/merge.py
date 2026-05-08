#!/usr/bin/env python3
"""
Profile merge logic — the deterministic core of autonomous learning.

Takes the current `profile.yaml` and a JSON list of pattern detections from
this `/coach-insights` run, applies the safeguards the debate converged on:

  - confidence math (+0.15 on recurrence, -0.05/day decay since last seen)
  - 2-of-3 run debounce (candidate → probationary)
  - 7-day probationary window (probationary → active)
  - absence-based graduation (active entry missing 5 runs in a row → retire)
  - low-confidence archive (confidence decay below floor → neutral archive)
  - bounded cap (max 10 active; lowest confidence×priority evicted)
  - atomic write (tempfile + os.replace) under flock

Inputs:
  --profile <path>      path to profile.yaml (will be mutated atomically)
  --changelog <path>    path to changelog.md (appended to)
  --lock <path>         flock path
  --detections <path>   JSON file: [{"id","name","nudge","examples",...}]
  --run-id <str>        stable identifier for this /coach-insights run (used in
                        per-entry run_appearances history and changelog)

Writes:
  - profile.yaml (atomic replace)
  - one new line in changelog.md describing adds/promotions/retirements
  - exits 0 on success, prints changelog line to stdout for the caller
  - non-zero exit on failure (/coach-insights skill then aborts, leaves profile intact)

This script does NOT call any LLM, does NOT read transcripts, does NOT judge
whether a detection is valid. All of that is the caller's responsibility.
The caller (the /coach-insights skill, running with a model in the loop) produces
the detections JSON; this script applies math and I/O.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# Shared heuristic for annotating promoted patterns with a reward_hint.
# reward_hints.py lives in this same directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from marker_io import atomic_marker_rmw_append  # noqa: E402
from reward_hints import infer_reward_hint  # noqa: E402
from xp_accounting import add_milestone_xp, normalize_profile_xp  # noqa: E402


# --- Tunables (the "config" is the constants in this file + the hook) -------
CONFIDENCE_ON_NEW = 0.20        # starting confidence for a brand-new detection
CONFIDENCE_BOOST = 0.15         # added when detected again in a run
CONFIDENCE_DECAY_PER_DAY = 0.05 # subtracted per day since last_seen_in_run
RETIRE_BELOW = 0.30             # active/probationary drops below → retire
GC_CANDIDATE_AFTER_DAYS = 14    # candidates that never debounce → gc'd
DEBOUNCE_THRESHOLD = 2          # "2 of last 3 runs" promotes candidate
DEBOUNCE_WINDOW = 3             # history window size
PROBATIONARY_DAYS = 7           # days before probationary → active
RETIRE_AFTER_ABSENT_RUNS = 5    # active missing this many consecutive runs → retire
POSITIVE_GRADUATION_RUNS = 5    # positive entry detected this many runs in a row → master
MAX_ACTIVE = 10                 # hard cap on active tier
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(dt: datetime) -> str:
    return dt.date().isoformat()


def _iso_dt(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        ss = str(s)
        if len(ss) == 10:
            return datetime.fromisoformat(ss).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(ss.replace("Z", "+00:00"))
    except Exception:
        return None


def load_profile(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "updated": None, "entries": [], "recent_runs": []}
    with path.open("r") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("schema_version", 1)
    data.setdefault("entries", [])
    data.setdefault("recent_runs", [])
    return data


def merge_skills_by_project(existing: dict, delta: dict) -> dict:
    """Add a per-project skill-invocation delta into the rolling
    accumulator. Both inputs are ``{project: {skill_id: count}}`` shape;
    counts are summed, projects are unioned. Returns a NEW dict — does
    not mutate ``existing``. Non-numeric counts are silently dropped to
    keep the cron path tolerant of stray/garbled state."""
    out: dict[str, dict[str, int]] = {}
    for proj, skills in (existing or {}).items():
        if not isinstance(skills, dict):
            continue
        out[str(proj)] = {
            str(k): int(v) for k, v in skills.items()
            if isinstance(v, (int, float))
        }
    for proj, skills in (delta or {}).items():
        if not isinstance(skills, dict):
            continue
        bucket = out.setdefault(str(proj), {})
        for sid, count in skills.items():
            if isinstance(count, (int, float)):
                bucket[str(sid)] = bucket.get(str(sid), 0) + int(count)
    return out


def atomic_write_yaml(path: Path, data: dict) -> None:
    tmp = tempfile.NamedTemporaryFile(
        "w", delete=False, dir=path.parent, prefix=".profile.", suffix=".tmp"
    )
    try:
        yaml.safe_dump(data, tmp, sort_keys=False, default_flow_style=False, allow_unicode=True)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    try:
        os.replace(tmp.name, path)
    except Exception:
        # os.replace can fail on cross-device renames or filesystem
        # quirks (rare — same dir + same fs is the design). Don't leave
        # an orphan .profile.*.tmp behind. Matches the cleanup pattern
        # in marker_io._atomic_write_under_lock and the hook's
        # _atomic_write_text.
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise


GRADUATION_MARKER = Path.home() / ".claude" / "coach" / ".pending_graduation"
STREAK_REWARD_MARKER = Path.home() / ".claude" / "coach" / ".pending_streak_rewards"

# Mid-streak XP schedule: streak N → +xp. 5/5 is graduation (+5) and is
# handled by the graduation path, not here, so skip 5. Applies to BOTH
# directions — weaknesses tick off clean_streak_runs (absence), strengths
# tick off positive_run_streak (presence).
STREAK_XP_SCHEDULE = {1: 1, 2: 1, 3: 1, 4: 2}
REGRESSION_MARKER = Path.home() / ".claude" / "coach" / ".pending_regression"


def _append_graduation_marker(new_graduations: list[dict], now: datetime) -> None:
    """Append graduated-this-run entries to the marker the UserPromptSubmit
    hook reads. Locked + atomic via marker_io so a stale-snapshot reader
    can't clobber the new entries with its own atomic-replace."""
    atomic_marker_rmw_append(GRADUATION_MARKER, "graduations", new_graduations, now)


def _append_streak_reward_marker(new_rewards: list[dict], now: datetime) -> None:
    """Mid-streak XP bumps. Written at /coach-insights time (one per pattern that
    ticked from streak N → N+1 for N in 0..3). Graduation (streak 4→5) is
    owned by the graduation marker, not this one."""
    atomic_marker_rmw_append(STREAK_REWARD_MARKER, "rewards", new_rewards, now)


def _append_regression_marker(new_regressions: list[dict], now: datetime) -> None:
    """Append regressed-this-run entries to the marker. Same locked
    read-modify-write semantics as the graduation marker."""
    atomic_marker_rmw_append(REGRESSION_MARKER, "regressions", new_regressions, now)


def merge(profile: dict, detections: list[dict], run_id: str, now: datetime) -> list[str]:
    """Mutate `profile` in place. Return a list of changelog fragments."""
    normalize_profile_xp(profile)
    entries: list[dict] = profile.get("entries", [])
    recent_runs: list[str] = profile.get("recent_runs", [])
    recent_runs = (recent_runs + [run_id])[-DEBOUNCE_WINDOW:]
    profile["recent_runs"] = recent_runs

    by_id = {e["id"]: e for e in entries if isinstance(e, dict) and "id" in e}
    detected_ids = {d["id"] for d in detections if isinstance(d, dict) and "id" in d}

    # One-shot back-fill: pre-existing entries written before reward_hint
    # existed get annotated on first merge. Idempotent; explicit hand-edits
    # are preserved because infer_reward_hint only fires when the field is
    # missing/invalid.
    for e in entries:
        if not isinstance(e, dict):
            continue
        if not isinstance(e.get("reward_hint"), dict):
            e["reward_hint"] = infer_reward_hint(e)

    log_fragments: list[str] = []
    regressions_this_run: list[dict] = []
    streak_rewards_this_run: list[dict] = []
    # Mutable single-cell accumulator (list so the inner scope can write to
    # it without `nonlocal`). Holds total mid-streak XP to bank this run.
    _streak_xp_earned_this_run = [0]

    # 0. Regression check: any detection whose id matches a currently-graduated
    #    NEGATIVE pattern means the user backslid on a weakness they had
    #    already retired. Revoke the graduation so the +5 lifetime XP is
    #    removed and re-add the pattern to entries as probationary — they
    #    have to re-earn mastery the same way they earned it the first time.
    #    (Positive-graduation revocation on sustained absence is a separate,
    #    more complex detection path — not implemented here yet.)
    graduated_profile: list[dict] = profile.get("graduated", []) or []
    graduated_by_id = {
        g.get("id"): g
        for g in graduated_profile
        if isinstance(g, dict) and g.get("id")
    }
    for did in list(detected_ids):
        g_entry = graduated_by_id.get(did)
        if not g_entry:
            continue
        if g_entry.get("direction", "negative") != "negative":
            continue  # positive graduations are not revoked via detection presence
        # Revoke: remove from graduated, re-add to entries as probationary.
        graduated_profile.remove(g_entry)
        graduated_by_id.pop(did, None)
        re_entry = {
            "id": did,
            "name": g_entry.get("name", did),
            "tier": "probationary",
            "direction": "negative",
            "confidence": 0.40,  # moderate — we already know this pattern is real
            "priority": 3,
            "nudge": "",  # filled in by the detection processing loop below
            "examples": [],
            "first_seen": _iso_date(now),
            "last_seen_in_run": _iso_date(now),
            "last_fired": None,
            "promoted_at": _iso_date(now),
            "source_runs": [],  # will be bumped to include run_id below
            "source_session_ids": [],
            "total_occurrences": 0,
            "clean_streak_runs": 0,
            "positive_run_streak": 0,
            # reward_hint carried over from the graduated record if present;
            # otherwise inferred below once nudge is populated.
            "reward_hint": g_entry.get("reward_hint") or None,
        }
        entries.append(re_entry)
        by_id[did] = re_entry
        log_fragments.append(f"⚠️{did}(regressed:re-detected-after-graduation)")
        regressions_this_run.append({
            "id": did,
            "name": g_entry.get("name", did),
            "direction": "negative",
            "originally_graduated_at": g_entry.get("graduated_at"),
            "originally_graduated_reason": g_entry.get("graduated_reason"),
        })
    profile["graduated"] = graduated_profile

    # 1. Update or insert entries from detections
    for det in detections:
        if not isinstance(det, dict) or "id" not in det:
            continue
        eid = det["id"]
        entry = by_id.get(eid)
        if entry is None:
            entry = {
                "id": eid,
                "name": det.get("name", eid),
                "tier": "candidate",
                "direction": det.get("direction", "negative"),
                "confidence": CONFIDENCE_ON_NEW,
                "priority": int(det.get("priority", 3)),
                "nudge": det.get("nudge", "").strip(),
                "examples": det.get("examples", [])[:3],
                "first_seen": _iso_date(now),
                "last_seen_in_run": _iso_date(now),
                "last_fired": None,
                "promoted_at": None,
                "source_runs": [run_id],
                "source_session_ids": det.get("source_session_ids", [])[:5],
                "total_occurrences": 1,
                "clean_streak_runs": 0,      # for negative graduation (absence)
                "positive_run_streak": 1,    # for positive graduation (presence)
            }
            # Inferred reward hint — id + nudge are populated, so we can
            # annotate right away. Explicit hints from detection dict win.
            entry["reward_hint"] = (
                det.get("reward_hint") if isinstance(det.get("reward_hint"), dict)
                else infer_reward_hint(entry)
            )
            entries.append(entry)
            by_id[eid] = entry
            log_fragments.append(f"+{eid}(candidate)")
            continue

        # Existing entry re-detected
        old_tier = entry.get("tier", "candidate")
        # Preserve direction; default to 'negative' for back-compat with
        # entries written by earlier merge versions.
        entry.setdefault("direction", det.get("direction", "negative"))
        entry["confidence"] = min(1.0, float(entry.get("confidence", 0)) + CONFIDENCE_BOOST)
        entry["last_seen_in_run"] = _iso_date(now)
        entry["total_occurrences"] = int(entry.get("total_occurrences", 0)) + 1
        entry["clean_streak_runs"] = 0
        old_positive_streak = int(entry.get("positive_run_streak", 0))
        new_positive_streak = old_positive_streak + 1
        entry["positive_run_streak"] = new_positive_streak

        # Mid-streak strength reward — mirror of the weakness-path reward in
        # the absence block below. Fires only for positive patterns on ticks
        # 1-4; streak 5 is mastery graduation, rewarded separately (+5 via
        # the positive-graduation block).
        if (
            entry.get("direction") == "positive"
            and new_positive_streak in STREAK_XP_SCHEDULE
            and old_positive_streak < new_positive_streak
        ):
            _strength_xp = STREAK_XP_SCHEDULE[new_positive_streak]
            streak_rewards_this_run.append({
                "id": entry["id"],
                "name": entry.get("name", entry["id"]),
                "streak": new_positive_streak,
                "target": 5,
                "xp_awarded": _strength_xp,
                "direction": "positive",
            })
            _streak_xp_earned_this_run[0] += _strength_xp
        entry["nudge"] = det.get("nudge", entry.get("nudge", "")).strip() or entry.get("nudge", "")
        # Back-fill reward_hint if missing (covers old entries from before this
        # field existed, plus regression re-entries that get their nudge set
        # here for the first time). Explicit hand-set hints are preserved.
        if not isinstance(entry.get("reward_hint"), dict):
            entry["reward_hint"] = (
                det.get("reward_hint") if isinstance(det.get("reward_hint"), dict)
                else infer_reward_hint(entry)
            )
        # Merge examples, dedupe, keep most recent 3
        new_examples = det.get("examples", []) or []
        if new_examples:
            seen: set[str] = set()
            merged: list[str] = []
            for ex in list(new_examples) + list(entry.get("examples", []) or []):
                k = str(ex).strip()
                if k and k not in seen:
                    seen.add(k)
                    merged.append(k)
            entry["examples"] = merged[:3]
        src_runs = entry.get("source_runs", [])
        src_runs = (src_runs + [run_id])[-DEBOUNCE_WINDOW:]
        entry["source_runs"] = src_runs
        sids_combined = list(entry.get("source_session_ids", []) or []) + list(det.get("source_session_ids", []) or [])
        seen_sids: set[str] = set()
        sids_dedup: list[str] = []
        for s in sids_combined:
            k = str(s)
            if k and k not in seen_sids:
                seen_sids.add(k)
                sids_dedup.append(k)
        entry["source_session_ids"] = sids_dedup[-10:]

        # Candidate → probationary (2-of-3 debounce)
        if old_tier == "candidate":
            hits_in_window = len(set(src_runs) & set(recent_runs))
            if hits_in_window >= DEBOUNCE_THRESHOLD:
                entry["tier"] = "probationary"
                entry["promoted_at"] = _iso_date(now)
                log_fragments.append(f"↑{eid}(probationary)")

    graduated: list[dict] = profile.get("graduated", []) or []
    graduated_start_len = len(graduated)  # for capturing graduations-this-run at end
    archived: list[dict] = profile.get("archived", []) or []

    # 1b. Check for POSITIVE graduations — positive entries that hit the
    # required consecutive-presence streak. Scan detected-this-run entries
    # (they just got their streak bumped in the detection loop above).
    for entry in list(entries):
        if entry.get("direction") != "positive":
            continue
        if entry["id"] not in detected_ids:
            continue
        tier = entry.get("tier", "candidate")
        if tier == "candidate":
            continue  # must debounce through candidate/probationary first
        streak = int(entry.get("positive_run_streak", 0))
        if streak >= POSITIVE_GRADUATION_RUNS:
            entries.remove(entry)
            graduated.append({
                "id": entry["id"],
                "name": entry.get("name", entry["id"]),
                "direction": "positive",
                "first_seen": entry.get("first_seen"),
                "last_seen_in_run": entry.get("last_seen_in_run"),
                "graduated_at": _iso_date(now),
                "graduated_reason": f"present-{streak}-runs",
                "total_occurrences": int(entry.get("total_occurrences", 0)),
                "final_tier": tier,
            })
            log_fragments.append(f"🎓{entry['id']}(graduated:strength-present-{streak}-runs)")

    # 2. Apply decay and absence-based retirement for entries NOT detected this run
    for entry in list(entries):
        eid = entry["id"]
        if eid in detected_ids:
            continue
        # Decay confidence by days since last_seen_in_run
        last_seen = _parse(entry.get("last_seen_in_run")) or _parse(entry.get("first_seen")) or now
        days = max(0, (now - last_seen).days)
        entry["confidence"] = max(0.0, float(entry.get("confidence", 0)) - CONFIDENCE_DECAY_PER_DAY * days)
        # Absence bumps clean-streak (good for NEGATIVE graduation) and
        # resets positive-streak (bad for POSITIVE entries — they need
        # consecutive presence).
        old_streak = int(entry.get("clean_streak_runs", 0))
        new_streak = old_streak + 1
        entry["clean_streak_runs"] = new_streak
        entry["positive_run_streak"] = 0

        # Mid-streak milestone reward. Fires only on a fresh tick (old → new
        # crossed a reward threshold), only for negative patterns (positive
        # graduation path is separate), and only for streaks 1-4. Streak 5+
        # is graduation and is rewarded via the graduation marker downstream.
        if (
            entry.get("direction", "negative") == "negative"
            and new_streak in STREAK_XP_SCHEDULE
            and old_streak < new_streak   # always true here, belt-and-braces
        ):
            _streak_xp = STREAK_XP_SCHEDULE[new_streak]
            streak_rewards_this_run.append({
                "id": entry["id"],
                "name": entry.get("name", entry["id"]),
                "streak": new_streak,
                "target": 5,
                "xp_awarded": _streak_xp,
                "direction": "negative",
            })
            _streak_xp_earned_this_run[0] += _streak_xp

        direction = entry.get("direction", "negative")
        tier = entry.get("tier", "candidate")
        if direction == "positive":
            # Positive patterns don't retire via absence or low confidence —
            # they just stop being credited. We only skip the retirement
            # logic below for them. They can still GC as candidates.
            if tier == "candidate" and days >= GC_CANDIDATE_AFTER_DAYS:
                entries.remove(entry)
                log_fragments.append(f"-{eid}(gc:never-debounced)")
            continue
        # Re-set local tier for the negative-retirement block below
        tier = entry.get("tier", "candidate")

        # Candidate GC: never debounced and old — remove quietly, not a graduation
        if tier == "candidate" and days >= GC_CANDIDATE_AFTER_DAYS:
            entries.remove(entry)
            log_fragments.append(f"-{eid}(gc:never-debounced)")
            continue

        # Absence-based retirement: missed too many consecutive runs → GRADUATE.
        # Uses clean_streak_runs (bumped above, unbounded) rather than a
        # loop over recent_runs, which was capped at DEBOUNCE_WINDOW=3 and
        # so could never reach RETIRE_AFTER_ABSENT_RUNS=5.
        absent_streak = int(entry.get("clean_streak_runs", 0))
        if tier in ("active", "probationary") and absent_streak >= RETIRE_AFTER_ABSENT_RUNS:
            entries.remove(entry)
            graduated.append({
                "id": eid,
                "name": entry.get("name", eid),
                "direction": "negative",
                "first_seen": entry.get("first_seen"),
                "last_seen_in_run": entry.get("last_seen_in_run"),
                "graduated_at": _iso_date(now),
                "graduated_reason": f"absent-{absent_streak}-runs",
                "total_occurrences": int(entry.get("total_occurrences", 0)),
                "final_tier": tier,
            })
            log_fragments.append(f"🎓{eid}(graduated:absent-{absent_streak}-runs)")
            continue

        # Confidence-floor retirement is a neutral archive, not a graduation.
        # The pattern has decayed below trust threshold; that is weaker
        # evidence than a demonstrated 5-run clean streak, so it should not
        # award graduation XP or fire a graduation celebration.
        if entry["confidence"] < RETIRE_BELOW and tier != "candidate":
            entries.remove(entry)
            archived.append({
                "id": eid,
                "name": entry.get("name", eid),
                "direction": "negative",
                "first_seen": entry.get("first_seen"),
                "last_seen_in_run": entry.get("last_seen_in_run"),
                "archived_at": _iso_date(now),
                "archive_reason": "low-confidence",
                "total_occurrences": int(entry.get("total_occurrences", 0)),
                "final_tier": tier,
                "final_confidence": float(entry.get("confidence", 0) or 0),
            })
            log_fragments.append(f"-{eid}(archived:low-confidence)")
            continue

    profile["graduated"] = graduated
    profile["archived"] = archived
    normalize_profile_xp(profile)

    # 3. Promote probationary → active after PROBATIONARY_DAYS
    for entry in entries:
        if entry.get("tier") != "probationary":
            continue
        promoted = _parse(entry.get("promoted_at"))
        if promoted and (now - promoted).days >= PROBATIONARY_DAYS:
            entry["tier"] = "active"
            entry["promoted_at"] = _iso_date(now)
            log_fragments.append(f"↑{entry['id']}(active)")

    # 4. Cap enforcement — keep at most MAX_ACTIVE active entries
    active = [e for e in entries if e.get("tier") == "active"]
    if len(active) > MAX_ACTIVE:
        active.sort(key=lambda e: float(e.get("confidence", 0)) * int(e.get("priority", 1)))
        to_evict = active[: len(active) - MAX_ACTIVE]
        for victim in to_evict:
            entries.remove(victim)
            log_fragments.append(f"-{victim['id']}(evicted:cap)")

    profile["updated"] = _iso_date(now)
    profile["entries"] = entries

    # Capture graduations from this run and write them to the
    # UserPromptSubmit marker so they can be celebrated.
    new_graduations = graduated[graduated_start_len:]
    if new_graduations:
        _append_graduation_marker(new_graduations, now)

    # Capture regressions from this run and write the regression marker.
    if regressions_this_run:
        _append_regression_marker(regressions_this_run, now)

    # Mid-streak milestone rewards: write marker + record the XP atomically
    # onto profile. Never fires for graduations (those earn +5 via
    # graduation_xp derived from current profile.graduated).
    if streak_rewards_this_run:
        _append_streak_reward_marker(streak_rewards_this_run, now)
        earned = int(_streak_xp_earned_this_run[0])
        if earned > 0:
            add_milestone_xp(profile, earned)
            log_fragments.append(f"+{earned}xp(mid-streak:{len(streak_rewards_this_run)})")
    else:
        normalize_profile_xp(profile)

    return log_fragments


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--changelog", required=True, type=Path)
    ap.add_argument("--lock", required=True, type=Path)
    ap.add_argument("--detections", required=True, type=Path)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--skill-hints", type=Path, default=None,
                    help="Optional JSON list; replaces profile.skill_hints snapshot. "
                         "Not subject to debounce/tier/decay — it's a static "
                         "installed-but-unused reference each run.")
    ap.add_argument("--skills-by-project-delta", type=Path, default=None,
                    help="Optional JSON {project: {skill_id: count}} delta "
                         "from this /coach-insights window. Accumulated into "
                         "profile.skills_by_project (the rolling per-project "
                         "invocation history that drives skill_inventory's "
                         "scope inference).")
    args = ap.parse_args()

    # Resolve marker output paths from the configured profile location
    # rather than the hardcoded ~/.claude/coach/ defaults at module
    # scope. Sandboxed runs (e.g. COACH_DIR_OVERRIDE in test_insights_llm.py)
    # pass --profile <tmp>/profile.yaml and need the .pending_*
    # markers to land under <tmp>/ too — pre-v0.5.1 the markers
    # leaked into the user's live install regardless of --profile.
    #
    # The module-level constants stay as defaults for direct callers
    # of merge() (e.g. the _marker_cleanup monkeypatch fixture in
    # test_merge.py); reassigning the globals here only affects the
    # CLI entry point.
    global GRADUATION_MARKER, STREAK_REWARD_MARKER, REGRESSION_MARKER
    coach_dir = args.profile.parent
    GRADUATION_MARKER = coach_dir / ".pending_graduation"
    STREAK_REWARD_MARKER = coach_dir / ".pending_streak_rewards"
    REGRESSION_MARKER = coach_dir / ".pending_regression"

    detections = json.loads(args.detections.read_text() or "[]")
    if not isinstance(detections, list):
        print("detections file must be a JSON array", file=sys.stderr)
        return 2

    skill_hints = None
    if args.skill_hints is not None:
        try:
            skill_hints = json.loads(args.skill_hints.read_text() or "[]")
            if not isinstance(skill_hints, list):
                skill_hints = None
        except Exception:
            skill_hints = None

    sbp_delta: dict = {}
    if args.skills_by_project_delta is not None:
        try:
            raw = json.loads(args.skills_by_project_delta.read_text() or "{}")
            if isinstance(raw, dict):
                sbp_delta = raw
        except Exception:
            sbp_delta = {}

    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lockfile:
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
        except OSError as e:
            print(f"flock failed: {e}", file=sys.stderr)
            return 3

        now = _now()
        profile = load_profile(args.profile)
        fragments = merge(profile, detections, args.run_id, now)

        if skill_hints is not None:
            prev = profile.get("skill_hints", []) or []
            prev_ids = {h.get("id") for h in prev if isinstance(h, dict)}
            new_ids = {h.get("id") for h in skill_hints if isinstance(h, dict)}
            added = new_ids - prev_ids
            dropped = prev_ids - new_ids
            profile["skill_hints"] = skill_hints
            if added:
                fragments.append(f"+hints:{len(added)}")
            if dropped:
                fragments.append(f"-hints:{len(dropped)}")

        if sbp_delta:
            existing_sbp = profile.get("skills_by_project") or {}
            updated_sbp = merge_skills_by_project(existing_sbp, sbp_delta)
            profile["skills_by_project"] = updated_sbp
            new_pairs = sum(
                1 for proj, skills in sbp_delta.items()
                if isinstance(skills, dict)
                for sid in skills
                if sid not in (existing_sbp.get(proj) or {})
            )
            if new_pairs:
                fragments.append(f"+sbp:{new_pairs}")

        atomic_write_yaml(args.profile, profile)

        changelog_line = f"- {_iso_dt(now)}: run={args.run_id} " + (
            " ".join(fragments) if fragments else "(no changes)"
        )
        with args.changelog.open("a") as cl:
            cl.write(changelog_line + "\n")

    print(changelog_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
