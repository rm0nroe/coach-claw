#!/usr/bin/env python3
"""
Emit a statusline segment: sigil + Roman numeral + ELO + level name.

XP comes from two places:

    LIFETIME (from ~/.claude/coach/profile.yaml):
        graduation_xp              — current graduated patterns × 5
        max_active_streak          — current longest clean streak, in runs
        session_banked_xp          — past sessions banked at 10:1
        milestone_xp               — mid-streak rewards from /coach-insights
        manual_adjustments         — explicit operator edits

    SESSION (from the active transcript, if passed in via statusline stdin):
        +2 per test-runner Bash invocation (pytest/jest/cargo/go test/...)
        +1 per `git commit`
        +1 per unique skill invoked (SlashCommand or Skill tool)

Session XP is capped at 15/session so it never dominates graduations.

Display vs bank ratio (whole-number + live)
-------------------------------------------
`bank.py` converts a session to lifetime XP at 10:1 (`session // 10`), so
a raw session ranges 0-15 but only 0-1 ever lands in lifetime. The `↑N`
arrow renders the **raw session count** (`↑5`, `↑15`) — always a whole
integer, always reflects live activity. The 10:1 bank ratio stays intact
internally; it's just not what the arrow displays.

Level vs rating are decoupled so the statusline is stable AND feels live:
  • Level index + level-up detection use `lifetime + session // 10`
    (integer — no phantom level-ups that get clawed back at session end).
  • Sigil color + ELO within-level slide use `lifetime + session / 10`
    (float — rating nudges on every test/commit, sigil glides through
    shades within a session even before a bank tick lands).

Level ladder: 50 unique levels (see LEVEL_NAMES below), starting at
Drafter (L1 / 0 xp) and ending at Origin (L50 / 5865 xp). L1-L8 thresholds
preserved from the original 8-level ladder so existing XP totals don't
trigger retroactive level-ups. After L8 each delta grows +5 per level.

Output (ANSI-colored):
    ◆ Ⅱ 1044 Iterator ↑5

    • ◆ sigil — bronze → silver → gold → platinum → diamond by within-level %
    • Roman numeral — bold, identifies the level (Ⅰ-Ⅼ, concatenated past Ⅹ)
    • ELO — linear 1000 (L1) → 2800 (L50), 4-digit feel
    • Level name — muted
    • ↑N — raw session XP in emerald (whole integer), only shown when session > 0

Silent (no output) when profile.yaml is missing AND session XP is 0.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from marker_io import atomic_marker_replace  # noqa: E402
from statusline_variants import Glyphs, render as render_variant  # noqa: E402
from themes import THEME_CRAFT, get_ladder  # noqa: E402
from user_config import load as load_user_config  # noqa: E402
from xp_accounting import normalize_profile_xp  # noqa: E402

PROFILE = Path.home() / ".claude" / "coach" / "profile.yaml"
LEVEL_STATE = Path.home() / ".claude" / "coach" / ".level_state.json"
LEVELUP_MARKER = Path.home() / ".claude" / "coach" / ".pending_levelup"

# Level-name ladder is theme-driven now (themes.py). The default theme
# ("craft") preserves the original 50 names so existing installs without
# a .user_config.json see no change. status.py imports LEVEL_NAMES via
# the same indirection so /coach status and the statusline can never
# disagree on what level you're at.
LEVEL_NAMES = THEME_CRAFT


def _build_level_ladder(names: list[str] | None = None) -> list[tuple[int, str]]:
    """50-level ladder with unique names per level. Preserves the original
    L1-L8 thresholds (0,3,8,15,25,40,60,90) exactly so existing XP totals
    don't trigger retroactive level-ups. After L8 the delta grows +5 per
    level, landing L50 at 5865 XP. The threshold curve is theme-independent."""
    names = names or LEVEL_NAMES
    fixed_deltas = [3, 5, 7, 10, 15, 20, 30]  # L1→L2 through L7→L8
    thresholds: list[int] = [0]
    for d in fixed_deltas:
        thresholds.append(thresholds[-1] + d)
    delta = 30
    while len(thresholds) < len(names):
        delta += 5
        thresholds.append(thresholds[-1] + delta)
    return list(zip(thresholds, names))


LEVELS = _build_level_ladder()

# ELO defaults: linear interpolation from 1000 at L1 → 2800 at L50, with
# a within-level slide so XP gains between level-ups also nudge the rating.
# Stays 4-digit across the entire ladder — real chess range. The user can
# override these via /config elo <min> <max> (~/.claude/coach/.user_config.json).
ELO_MIN = 1000
ELO_MAX = 2800

# Per-process snapshot of the user's selected theme + variant + ELO range.
# Read once at module import — slash-command edits to .user_config.json
# take effect on the next process invocation (every statusline render is
# a fresh process, so this is immediate from the user's perspective).
def _load_runtime_config() -> tuple[list[tuple[int, str]], int, int, str]:
    """Returns (LEVELS, ELO_MIN, ELO_MAX, statusline_variant) keyed off
    ~/.claude/coach/.user_config.json. Falls back to compiled defaults on
    any error so the statusline always renders."""
    try:
        cfg = load_user_config()
        ladder = _build_level_ladder(get_ladder(cfg["theme"]))
        return ladder, cfg["elo_min"], cfg["elo_max"], cfg["statusline_variant"]
    except Exception:
        return _build_level_ladder(THEME_CRAFT), ELO_MIN, ELO_MAX, "crystal"


LEVELS, ELO_MIN, ELO_MAX, STATUSLINE_VARIANT = _load_runtime_config()

BAR_SEGMENTS = 10
SESSION_XP_CAP = 15

# Palette from ~/.claude/statusline-command.sh, plus a coach-specific
# empty-segment color. The statusline's DIM_STEEL (#3A3A4A) is almost
# invisible against a dark terminal background when nothing's adjacent
# to provide contrast — a fully-empty bar looks blank. MUTED_STEEL is
# bright enough for the ▱ glyphs to always read, dim enough that it
# still visually subordinates to filled segments.
ICE_SILVER  = "\x1b[38;2;200;214;229m"
DIM_STEEL   = "\x1b[38;2;58;58;74m"
MUTED_STEEL = "\x1b[38;2;110;122;140m"
RESET       = "\x1b[0m"

# Sub-rank sigil color — the ◆ prefix cycles through bronze → silver → gold
# → platinum → diamond as the user progresses within their current level.
# Resets to bronze on level-up. The Roman numeral + ELO number stay neutral
# so the sigil color carries all the within-level progress signal.
SIGIL_BRONZE    = "\x1b[38;2;205;127;50m"    # 0–20% through level
SIGIL_SILVER    = "\x1b[38;2;200;205;215m"   # 20–40%
SIGIL_GOLD      = "\x1b[38;2;245;197;66m"    # 40–60%
SIGIL_PLATINUM  = "\x1b[38;2;180;220;235m"   # 60–80%
SIGIL_DIAMOND   = "\x1b[38;2;150;240;255m"   # 80–100% / max level

GAIN_EMERALD    = "\x1b[38;2;90;218;170m"    # ↑N session-gain arrow

# Unicode Roman numeral converter for 1..50.
# Uses concatenation (Ⅹ + Ⅲ → ⅩⅢ) rather than single-char forms (Ⅺ, Ⅻ)
# so the aesthetic is consistent across the full ladder.
_ROMAN_TENS  = ["", "Ⅹ", "ⅩⅩ", "ⅩⅩⅩ", "ⅩⅬ", "Ⅼ"]
_ROMAN_UNITS = ["", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ"]

def _to_roman(n: int) -> str:
    n = max(1, min(n, 50))
    return _ROMAN_TENS[n // 10] + _ROMAN_UNITS[n % 10]


def _level_for(xp: float) -> tuple[int, str, int, int | None]:
    """Return (level_index, name, current_threshold, next_threshold_or_None)."""
    idx = 0
    for i, (thr, _) in enumerate(LEVELS):
        if xp >= thr:
            idx = i
    name = LEVELS[idx][1]
    cur = LEVELS[idx][0]
    nxt = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None
    return idx, name, cur, nxt


def _compute_hybrid(lifetime: int, session: int) -> dict:
    """Core hybrid math — exposed for testing. See module docstring.

    Returns a dict with the keys downstream rendering needs:
      level_xp, progress_xp, idx, name, cur, nxt, elo, sigil_pct
    """
    bank_gain = session // 10
    level_xp = lifetime + bank_gain
    progress_xp = lifetime + session / 10
    idx, name, cur, nxt = _level_for(level_xp)
    max_idx = len(LEVELS) - 1
    base = ELO_MIN + (idx / max_idx) * (ELO_MAX - ELO_MIN) if max_idx > 0 else ELO_MIN
    if nxt is None:
        elo = ELO_MAX
        sigil_pct = 1.0
    else:
        span = max(1, nxt - cur)
        within_pct = max(0.0, min(1.0, (progress_xp - cur) / span))
        nxt_base = ELO_MIN + ((idx + 1) / max_idx) * (ELO_MAX - ELO_MIN)
        elo = int(round(base + within_pct * (nxt_base - base)))
        sigil_pct = within_pct
    return {
        "level_xp": level_xp, "progress_xp": progress_xp,
        "idx": idx, "name": name, "cur": cur, "nxt": nxt,
        "elo": elo, "sigil_pct": sigil_pct,
    }


def compute_for_render(lifetime: int, session: int) -> dict:
    """Public render-side helper for banner shapes that need rank metadata.

    Returns a flat dict with what a renderer typically asks for:
      idx          — 0-based level index
      name         — theme-aware level name (from active LEVELS ladder)
      elo          — interpolated ELO within the current level
      roman        — Unicode Roman numeral for level (1..50 → Ⅰ..Ⅼ)
      medal_count  — 1 + idx // 4, capped at 5 (rank-ribbon scaling)
      next_xp      — XP threshold for the next level, or None at L50
    """
    h = _compute_hybrid(lifetime, session)
    idx = h["idx"]
    return {
        "idx": idx,
        "name": h["name"],
        "elo": h["elo"],
        "roman": _to_roman(idx + 1),
        "medal_count": min(5, idx // 4 + 1),
        "next_xp": h["nxt"],
    }


def _atomic_write_json_under_lock(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(payload))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise


def _check_levelup(current_idx: int, current_name: str, xp: int) -> None:
    """Detect level-up vs last-seen state. Write a marker file on transition
    for the UserPromptSubmit hook to surface as a celebration. First-time
    initialization: if the user is already above Drafter, write the marker
    so they get a retroactive celebration (then future ups are detected
    normally)."""
    try:
        LEVEL_STATE.parent.mkdir(parents=True, exist_ok=True)
        lock_path = LEVEL_STATE.with_suffix(LEVEL_STATE.suffix + ".lock")
        with open(lock_path, "w") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass

            last_state = None
            if LEVEL_STATE.exists():
                try:
                    last_state = json.loads(LEVEL_STATE.read_text())
                except Exception:
                    last_state = None

            # `created_at` + `consumed_by` are read by the UserPromptSubmit
            # hook's `_read_and_consume()` so concurrent Claude Code sessions
            # each see the level-up celebration once. atomic_marker_replace
            # serializes the write under the same sidecar flock the reader
            # uses, so a stale-snapshot reader can't clobber a fresh level-up.
            now_iso = datetime.now(timezone.utc).isoformat()

            if last_state is None:
                # First run ever. If they're already above Drafter (L1), treat as
                # pending celebration so they don't miss it retroactively.
                if current_idx > 0:
                    from_name = LEVELS[0][1]
                    atomic_marker_replace(LEVELUP_MARKER, {
                        "from": from_name,
                        "from_idx": 0,
                        "to": current_name,
                        "to_idx": current_idx,
                        "xp_at_levelup": xp,
                        "created_at": now_iso,
                        "consumed_by": [],
                    })
                _atomic_write_json_under_lock(LEVEL_STATE, {"level_idx": current_idx})
                return

            last_idx = int(last_state.get("level_idx", 0))
            if current_idx > last_idx:
                from_name = LEVELS[last_idx][1] if 0 <= last_idx < len(LEVELS) else "?"
                atomic_marker_replace(LEVELUP_MARKER, {
                    "from": from_name,
                    "from_idx": last_idx,
                    "to": current_name,
                    "to_idx": current_idx,
                    "xp_at_levelup": xp,
                    "created_at": now_iso,
                    "consumed_by": [],
                })
                _atomic_write_json_under_lock(LEVEL_STATE, {"level_idx": current_idx})
            # NOTE: we never write state downward. Transient low-XP reads
            # (statusline renders before the current transcript has flushed
            # recent tool uses) used to regress the state, which then made
            # the *next* read look like a fresh level-up and re-trigger the
            # celebration. State is a high-water mark; only raise it.
    except Exception:
        # Never let level-up tracking break statusline rendering.
        pass


def _read_stdin_json() -> dict:
    """Parse the Claude Code statusline JSON from stdin, if any."""
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _find_transcript_path(payload: dict) -> Path | None:
    # Claude Code passes varying shapes; try common ones.
    for key in ("transcript_path", "transcript", "session_transcript"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            p = Path(v).expanduser()
            if p.exists():
                return p
    # Nested shapes
    session = payload.get("session") or {}
    if isinstance(session, dict):
        v = session.get("transcript_path") or session.get("transcript")
        if isinstance(v, str) and v:
            p = Path(v).expanduser()
            if p.exists():
                return p
    return None


def _session_xp_from_transcript(path: Path, profile: dict | None = None) -> int:
    """Scan the transcript JSONL for reward-eligible tool uses.

    Baseline XP (test_run/commit/skill_invoke) plus any dynamic actions
    declared via reward_hint on profile entries. See scoring.py for the
    single source of truth."""
    # scoring.py lives next to us; import lazily so stats.py startup stays fast.
    from scoring import score_transcript
    return score_transcript(path, profile)


def _lifetime_xp() -> int:
    if not PROFILE.exists():
        return 0
    try:
        import yaml
        data = yaml.safe_load(PROFILE.read_text()) or {}
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    return int(normalize_profile_xp(data)["lifetime_xp"])


def render_segment(payload: dict | None = None) -> str:
    """Compose the coach statusline segment from a parsed payload.

    Returns the rendered ANSI string, or an empty string when the coach
    has nothing to display (no profile file AND no transcript-derived
    session signal). Side effect: detects level transitions and writes
    the levelup marker for the UserPromptSubmit hook.

    Public callable consumed by `default_statusline.py` (which composes
    model + context-bar + this segment in-process). The CLI entrypoint
    `main()` is now a thin wrapper around this function.
    """
    if payload is None:
        payload = {}

    lifetime = _lifetime_xp()
    session = 0
    tpath = _find_transcript_path(payload)
    if tpath is not None:
        # Load the profile (if readable) so dynamic reward_hint actions score too.
        _profile_for_scoring: dict | None = None
        try:
            if PROFILE.exists():
                import yaml
                _profile_for_scoring = yaml.safe_load(PROFILE.read_text()) or {}
        except Exception:
            _profile_for_scoring = None
        session = _session_xp_from_transcript(tpath, _profile_for_scoring)

    # Stay silent if the coach isn't set up at all (no profile file) AND
    # we got no transcript-derived signal either.
    if not PROFILE.exists() and session == 0:
        return ""

    h = _compute_hybrid(lifetime, session)
    idx, name, cur, nxt = h["idx"], h["name"], h["cur"], h["nxt"]
    elo = h["elo"]

    # Side effect: detect level transitions and write a marker for the
    # UserPromptSubmit hook to surface as a visible celebration. Keyed off
    # level_xp so celebrations fire only on actual (banked) level changes.
    _check_levelup(idx, name, h["level_xp"])

    # Sub-rank sigil tier: bronze → silver → gold → platinum → diamond.
    # Uses the float within-level pct (progress_xp) so the sigil glides
    # through shades within a session even when no bank tick has landed.
    pct = h["sigil_pct"]
    if nxt is None or pct >= 0.8:
        sigil_tier = "diamond"
    elif pct >= 0.6:
        sigil_tier = "platinum"
    elif pct >= 0.4:
        sigil_tier = "gold"
    elif pct >= 0.2:
        sigil_tier = "silver"
    else:
        sigil_tier = "bronze"

    # Display the raw session count as a whole integer (0-15). The 10:1
    # bank ratio still applies internally (level_xp uses session // 10,
    # ELO slide uses session / 10), but the arrow shows session points so
    # every test/commit produces an immediate, readable bump.
    glyphs = Glyphs(
        level=idx + 1,
        name=name,
        elo=elo,
        session_xp=session,
        sigil_tier=sigil_tier,
        bar_pct=pct,
    )
    return render_variant(STATUSLINE_VARIANT, glyphs)


def main() -> int:
    try:
        out = render_segment(_read_stdin_json())
        if out:
            sys.stdout.write(out)
    except Exception:
        # Failsafe: same contract as default_statusline.main —
        # a render-path crash must not break Claude Code's statusline
        # or noise up the terminal. Emit nothing; exit 0.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
