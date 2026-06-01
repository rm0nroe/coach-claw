#!/usr/bin/env python3
"""
Coach Claw — UserPromptSubmit hook.

Two responsibilities on every user prompt:

  1. Celebration banners (one-shot). Reads three marker files written by
     upstream processes and injects banner-render instructions, then clears
     the markers.

       ~/.claude/coach/.pending_levelup     — XP threshold crossed
       ~/.claude/coach/.pending_graduation  — pattern graduated
       ~/.claude/coach/.pending_regression  — graduated pattern regressed

  2. Scheduled ambient tips (restored 2026-04-19). Rolls a dice per prompt;
     when it lands, picks one tip from profile.yaml entries + skill_hints,
     rotates through an emoji label pool, and injects a REQUIRED render
     instruction. Replaces the archived coach-stop.py prototype — same
     selection logic but delivered through UserPromptSubmit's proven
     additionalContext channel (the Stop hook's systemMessage renders as a
     warning, wrong vibe; /dev/tty rendered below the input prompt, wrong
     slot). State tracked in ~/.claude/coach/.tip_state.json.

Design invariants:
  - Always exits 0. A broken coach must never block a prompt.
  - Emits valid JSON or nothing. No stderr leakage into the UI.
  - Reads markers + clears them (celebrations) or reads + writes tip state
    (scheduler). Never mutates profile.yaml — that's /coach-insights' job.
"""
from __future__ import annotations

import fcntl
import json
import os
import random
import re
import sys
import tempfile
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Resolve the coach state dir BEFORE adding bin/ to sys.path — the helper
# we'd otherwise import (`coach_paths.resolve_coach_dir`) lives in that
# very dir, so we have to inline the env-var contract here. Keep this in
# sync with `coach/bin/coach_paths.py:resolve_coach_dir()`.
_COACH_BASE = os.environ.get("COACH_CONFIG_DIR")
COACH_DIR = Path(_COACH_BASE) if _COACH_BASE else Path.home() / ".claude" / "coach"

# Shared modules — locate the bin/ that ships with THIS hook copy.
#   - Plugin context: ${CLAUDE_PLUGIN_ROOT}/bin/  (set by Claude Code).
#   - CLI context: ${COACH_DIR}/bin/.
# Without this branch, the plugin's hook would pull from the CLI install's
# stale bin/ (which can be missing newer modules like cron_check or
# statusline_self_patch — observed during e2e validation 2026-05-09).
_PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
if _PLUGIN_ROOT:
    sys.path.insert(0, str(Path(_PLUGIN_ROOT) / "bin"))
else:
    sys.path.insert(0, str(COACH_DIR / "bin"))
try:
    from reward_hints import (  # noqa: E402
        infer_reward_hint as _shared_infer_reward_hint,
        effective_reward_hint as _shared_effective_reward_hint,
    )
    _SHARED_HINT_OK = True
except Exception:
    # If the shared module is missing, fall back to the local inline heuristic
    # below so the hook never crashes. Never blocks a user prompt.
    _SHARED_HINT_OK = False
try:
    from scoring import matches_action as _shared_matches_action  # noqa: E402
    _SHARED_SCORING_OK = True
except Exception:
    _SHARED_SCORING_OK = False
try:
    from render_env import detect_render_env  # noqa: E402
except Exception:
    # Defensive: if the helper is missing, force terminal shape so the
    # current rendering path always works.
    def detect_render_env(env=None):  # type: ignore[no-redef]
        return "terminal"
try:
    from banner_themes import (  # noqa: E402
        render_celebrate_for_theme as _render_celebrate_for_theme,
        BESPOKE_THEMES as _BESPOKE_THEMES,
    )
    _BESPOKE_OK = True
except Exception:
    _BESPOKE_OK = False
    _BESPOKE_THEMES = frozenset()
try:
    from user_config import get_theme as _get_theme  # noqa: E402
except Exception:
    def _get_theme():  # type: ignore[no-redef]
        return "craft"
try:
    from display_names import display_name as _display_name  # noqa: E402
except Exception:
    def _display_name(entry_id, profile=None, *, positive_frame: bool = False):  # type: ignore[no-redef]
        # Degraded fallback when display_names import fails. Accepts
        # positive_frame so callers don't crash, but cannot perform the
        # inverse lookup — humanized slug is the best we can do.
        del positive_frame
        if not entry_id:
            return entry_id
        return entry_id.replace("-", " ")
PROFILE = COACH_DIR / "profile.yaml"
LEVELUP_MARKER = COACH_DIR / ".pending_levelup"
GRADUATION_MARKER = COACH_DIR / ".pending_graduation"
REGRESSION_MARKER = COACH_DIR / ".pending_regression"
STREAK_REWARD_MARKER = COACH_DIR / ".pending_streak_rewards"
WRAP_ANNOUNCE_MARKER = COACH_DIR / ".statusline-wrap-announced"
WRAP_DUPLICATE_MARKER = COACH_DIR / ".statusline-wrap-duplicate-detected"
DISABLED_FLAG = COACH_DIR / ".disabled"
TIP_STATE = COACH_DIR / ".tip_state.json"
LOG_PATH = COACH_DIR / "log.ndjson"
LOG_MAX_LINES = 500
# How long to keep one-shot celebration markers around. Each session
# consumes a marker once (per-session dedup via `consumed_by`); TTL just
# bounds how long an unconsumed marker lingers if all sessions go idle.
MARKER_TTL_HOURS = 24
MARKER_CONSUMED_BY_CAP = 100

# --- Tip scheduler tuning (dial here, not via env) ---
TIP_FIRE_PROBABILITY = 0.35      # per-prompt roll after cooldown passes
TIP_GLOBAL_COOLDOWN_SEC = 300    # min seconds between any two tips
TIP_PER_TIP_COOLDOWN_HOURS = 24  # same tip id won't repeat within this window

# --- Weighted selection tuning (Fix 4) ---
# Drives which eligible tip gets picked when multiple are ready. Baseline
# weight = confidence × priority (mirrors merge.py:422's cap-eviction rule).
# Then multiplied by tier + streak-urgency factors. Constants are knobs.
TIER_MULTIPLIER = {
    "probationary": 1.5,   # new pattern, user hasn't seen it yet — surface it
    "active":       1.0,   # steady state
    "hint":         0.4,   # skill hints — nice-to-have, below weaknesses
}
STREAK_URGENCY_HIGH = 1.3       # streak 0-1: early, encourage pickup
STREAK_URGENCY_MID = 1.0        # streak 2-3: midway
STREAK_URGENCY_LOW = 0.6        # streak 4: near graduation, user's already doing it
STRENGTH_WEIGHT_MULTIPLIER = 0.75  # reinforcement should appear, not drown out fixes

# Skill-share floor. Without this, heavy-weakness profiles can starve skill
# hints — cumulative weakness weight drowns the hint multiplier (0.4×) so
# skills practically never surface. If ANY skill hints are eligible, the
# scheduler scales their weights up so they collectively receive at least
# this share of total weight.
MIN_SKILL_SHARE = 0.25

# --- Reward attribution (keeps the reward loop visible in every tip) ---
# Source of truth for action→XP math is ~/.claude/coach/bin/stats.py:240:
#   xp = test_runs * 2 + commits * 1 + len(unique_skills) * 1
# and lifetime adds +5 per graduated pattern (5-run clean streak).
GRADUATION_XP = 5
GRADUATION_STREAK_TARGET = 5
SKILL_XP_PER_UNIQUE = 1   # +1 once per skill per session
SESSION_XP_CAP = 15       # hard ceiling, per stats.py SESSION_XP_CAP

# Test-runner + commit regexes — MUST stay in lockstep with stats.py copies
# so completion detection matches what actually earns the XP there.
# Position-anchored (start-of-line or after ; && || |) with optional env-var
# or `cd … &&` prefix — prevents false positives on these tokens when they
# appear inside commit message bodies. Mirror any edit across both files.
TEST_RE = re.compile(
    r"(?:^|[;&|])\s*"
    r"(?:\w+=\S+\s+)*"
    r"(?:cd\s+\S+\s*&&\s*)?"
    r"(?:pytest|jest|vitest|mocha|rspec|phpunit|"
    r"cargo\s+test|go\s+test|pnpm\s+test|npm\s+test|bun\s+test|"
    r"yarn\s+test|mix\s+test)"
    r"\b"
)
COMMIT_RE = re.compile(
    r"(?:^|[;&|])\s*"
    r"(?:\w+=\S+\s+)*"
    r"(?:cd\s+\S+\s*&&\s*)?"
    r"git\s+commit\b"
)

# --- Dynamic reward attribution (profile-driven) ---
# The reward for following a tip is derived from each profile.yaml entry's
# `reward_hint` field. Shape:
#
#   reward_hint:
#     action: test_run | commit | skill_invoke
#     xp: 2
#     description: "test run (pytest / jest / …)"
#
# When `reward_hint` is missing on an entry, _infer_reward_hint() falls back
# to a keyword heuristic over the entry id. This lets new patterns earn XP
# without a code edit — /coach-insights can populate reward_hint at promotion time,
# or we infer at read time. Patterns where neither matches are graduation-only
# (only reward is the +5 lump sum at 5 clean /coach-insights runs).
#
# Keep this list aligned with stats.py's scored actions so the TIP's promised
# XP actually gets awarded:
#   - test_run     → +2 via TEST_RE bash-command match (stats.py same regex)
#   - commit       → +1 via COMMIT_RE bash-command match (stats.py same regex)
#   - skill_invoke → +1 once per unique /skill per session (skill tips only)

_REWARD_HINT_HEURISTIC: list[tuple[str, dict]] = [
    # (id-substring, reward_hint payload). First match wins.
    ("without-test",   {"action": "test_run", "xp": 2,
                        "description": "test run (pytest / jest / cargo test / …)"}),
    ("untested",       {"action": "test_run", "xp": 2,
                        "description": "test run"}),
    ("skip-test",      {"action": "test_run", "xp": 2,
                        "description": "test run"}),
    ("without-commit", {"action": "commit",   "xp": 1,
                        "description": "git commit"}),
]


def _infer_reward_hint(entry_id: str) -> dict | None:
    """Local fallback if shared reward_hints module isn't importable.
    Same logic as reward_hints.infer_reward_hint but id-only, no nudge text."""
    if not entry_id:
        return None
    eid = entry_id.lower()
    for keyword, hint in _REWARD_HINT_HEURISTIC:
        if keyword in eid:
            return dict(hint)
    return None


def _effective_reward_hint(entry: dict) -> dict | None:
    """Pull reward_hint from the profile entry, else infer. Prefers the
    shared reward_hints module (which also inspects nudge text) and falls
    back to a local id-only heuristic if the import failed."""
    if _SHARED_HINT_OK:
        return _shared_effective_reward_hint(entry)
    explicit = entry.get("reward_hint")
    if (
        isinstance(explicit, dict)
        and explicit.get("action")
        and int(explicit.get("xp", 0)) > 0
    ):
        return explicit
    return _infer_reward_hint(entry.get("id") or "")

# Label pools (weakness vs skill flavor). ~60% carry a content-matched emoji.
# Kept in the hook so selection is deterministic — Claude shapes the BODY,
# the hook picks the LABEL so rotation actually happens.
WEAKNESS_LABELS = [
    # Coach-themed / professional (primary)
    "*Tip:*",
    "*Pointer:*",
    "*Heads up:*",
    "*Worth noting:*",
    # Subject-matched emoji (coach voice, occasional visual)
    "*🎯 Tip:*",           # focus / precision
    "*✏️ Tip:*",           # testing / verification
    "*🧭 Heads up:*",      # navigation / direction
    "*🪶 Worth noting:*",  # simplification
    "*📌 Worth noting:*",  # durability / pin for later
    # Occasional quest flavor (rare — sprinkle, not theme)
    "*🎯 Quest:*",
]
SKILL_LABELS = [
    # Coach-themed / professional (primary)
    "*Coach:*",
    "*🦞 From Coach Claw:*",
    "*🦞 Coach:*",
    # Occasional quest flavor
    "*🌟 Power-up:*",
]
STRENGTH_LABELS = [
    "*Strength:*",
    "*Keep:*",
    "*Good pattern:*",
    "*⚔️ Strength:*",
    "*📌 Keep:*",
]


def _ide_label(label: str) -> str:
    """Convert a terminal-shape label (e.g. `*Tip:*`, `*🦞 From Coach Claw:*`,
    `*🎯 Tip:*`) to an IDE-shape label (e.g. `🦞 **Tip**`,
    `🦞 **From Coach Claw**`).

    Always prefixes with 🦞 as the universal coach persona signature. Drops
    content-matched emoji from the label content since IDE banners get their
    visual signature from the HR frame + 🦞 + bold + code-span pills, not
    from per-tip emoji rotation.
    """
    text = label.strip().strip("*").rstrip(":").strip()
    parts = text.split(" ", 1)
    if len(parts) == 2 and not parts[0].isascii():
        text = parts[1]
    return f"🦞 **{text}**"


def _emit(context: str | None) -> None:
    if context:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def _disabled() -> bool:
    if os.environ.get("COACH_DISABLE") == "1":
        return True
    return DISABLED_FLAG.exists()


def _read_and_consume(path: Path, session_key: str | None, now: datetime) -> dict | None:
    """Read a one-shot celebration marker, returning its payload exactly
    once per `session_key`.

    Multi-session safe (was BACKLOG P2): the marker JSON carries a
    `consumed_by` list of session keys plus a `created_at` ISO timestamp.
    Each concurrent Claude Code session that polls sees the marker once;
    subsequent polls from the same session return None. Markers are
    auto-cleaned after MARKER_TTL_HOURS so abandoned markers don't
    accumulate. The read-modify-write is serialized via a sidecar flock
    so two sessions polling at the same instant can't both append-and-
    overwrite each other's `consumed_by` entry.

    Backwards-compat: legacy markers without `created_at` / `consumed_by`
    (written by v0.1.x) are stamped on first read and treated as fresh.
    """
    if not path.exists():
        return None
    key = session_key or "unknown"
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with open(lock_path, "w") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except Exception as exc:
                if os.environ.get("COACH_DEBUG"):
                    print(
                        f"coach: marker flock failed for {path.name}: {exc}",
                        file=sys.stderr,
                    )
            # Re-check existence under lock — another session may have
            # TTL-cleaned it between the outer existence check and here.
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text())
            except Exception:
                # Corrupt marker — clean up so we don't loop on it.
                try:
                    path.unlink()
                except Exception:
                    pass
                return None
            if not isinstance(data, dict):
                try:
                    path.unlink()
                except Exception:
                    pass
                return None

            consumed_by = data.get("consumed_by")
            if not isinstance(consumed_by, list):
                consumed_by = []

            # TTL cleanup. Aged markers are deleted on the next poll from
            # any session, regardless of whether that session has seen them.
            created_at = data.get("created_at")
            if isinstance(created_at, str):
                try:
                    created_dt = datetime.fromisoformat(created_at)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    if (now - created_dt) > timedelta(hours=MARKER_TTL_HOURS):
                        try:
                            path.unlink()
                        except Exception:
                            pass
                        return None
                except Exception:
                    pass

            # Already consumed by this session — stay silent so the same
            # banner doesn't render on every prompt for the rest of the day.
            if key in consumed_by:
                return None

            # First-time consumption: append, cap at MARKER_CONSUMED_BY_CAP
            # (drop oldest), atomic-rewrite the marker.
            consumed_by.append(key)
            if len(consumed_by) > MARKER_CONSUMED_BY_CAP:
                consumed_by = consumed_by[-MARKER_CONSUMED_BY_CAP:]
            data["consumed_by"] = consumed_by
            if not isinstance(data.get("created_at"), str):
                data["created_at"] = now.isoformat()
            try:
                _atomic_write_text(path, json.dumps(data, sort_keys=True))
            except Exception:
                # Best-effort: if rewrite fails, still surface the banner
                # this once. Worst case is the banner repeats next prompt.
                pass
            return data
    except Exception:
        return None


def _plugin_install_descriptor() -> tuple[str, str]:
    """Return (version, install_path) for the active plugin install.

    Pulls from CLAUDE_PLUGIN_ROOT env (set by Claude Code on every
    plugin hook invocation). Falls back to ("?", "?") if the env is
    missing or unparseable — the banner is best-effort context, not
    load-bearing for correctness."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not root:
        return ("?", "?")
    # CLAUDE_PLUGIN_ROOT format:
    #   ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>
    # Version is the basename. Keep install path as-is for copy-paste.
    try:
        version = Path(root).name or "?"
    except Exception:
        version = "?"
    return (version, root)


def _cron_nudge_block(env: str = "terminal") -> str:
    """Pre-rendered one-time banner pointing plugin-only users at the
    npm CLI for OS-level cron registration. The plugin model can't
    register launchd/cron itself; without the cron, profile.yaml never
    gets the daily deterministic refresh."""
    title = "Daily insights need OS scheduling"
    version, install_path = _plugin_install_descriptor()
    macos_cmd = "npx @rm0nroe/coach-claw launchd"
    linux_cron = (
        "0 4 * * * $HOME/.claude/coach/bin/insights.sh 1d "
        ">> /tmp/claude-coach.log 2>&1"
    )
    verify = "tail -f /tmp/claude-coach.log"
    if env == "ide":
        body = (
            f"📅 **{title}**\n\n"
            f"Coach Claw plugin v{version} installed at `{install_path}`.\n\n"
            "The plugin can't register OS schedulers on its own. "
            "Without one, `profile.yaml` only refreshes on the weekly "
            "LLM-driven path (~every 7d).\n\n"
            f"Register the daily cron:\n\n"
            f"  macOS: `{macos_cmd}`\n\n"
            f"  Linux: `crontab -e` then add `{linux_cron}`\n\n"
            f"Verify after next 04:00 local: `{verify}`\n\n"
            "This nudge fires once per install."
        )
        return _hr_frame_stack([body])
    return (
        f"> 📅 **{title}**\n"
        ">\n"
        f"> Coach Claw plugin v{version} installed at\n"
        f"> `{install_path}`.\n"
        ">\n"
        "> The plugin can't register OS schedulers on its own. "
        "Without one,\n"
        "> `profile.yaml` only refreshes on the weekly LLM-driven "
        "path (~every 7d).\n"
        ">\n"
        "> Register the daily cron:\n"
        ">\n"
        "> ```\n"
        f"> {macos_cmd}     # macOS\n"
        f"> # Linux: crontab -e then add:\n"
        f"> # {linux_cron}\n"
        "> ```\n"
        ">\n"
        f"> Verify after next 04:00 local: `{verify}`.\n"
        "> This nudge fires once per install."
    )


def _maybe_cron_nudge_block(env: str = "terminal") -> str | None:
    """Emit the cron nudge once, if running under the plugin AND no
    cron is registered AND we haven't nudged before. Otherwise None.

    Always failsafe — any exception returns None so the hook keeps
    rendering normally.
    """
    if not os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return None
    try:
        marker = COACH_DIR / ".cron-nudged"
        if marker.exists():
            return None
        from cron_check import is_cron_registered
        if is_cron_registered():
            return None
        banner = _cron_nudge_block(env)
        # Persist marker so the nudge fires exactly once. Ignore write
        # failures — re-nudging on the next prompt is a minor regression,
        # not a correctness bug.
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({
                "nudged_at": datetime.now(timezone.utc).isoformat(),
            }))
        except Exception:
            pass
        # Wrap in <coach-install-summary> with explicit surface-verbatim
        # instructions. Without this framing, the model treats the banner
        # as ambient context and buries it — same bug class as the
        # pre-v0.4.1 delegated-rendering issue documented in CLAUDE.md
        # under "Celebrate banners are pre-rendered verbatim".
        return (
            "<coach-install-summary>\n"
            "This is a one-shot post-install summary for the Coach "
            "Claw plugin. The banner below was pre-rendered by the "
            "install-nudge hook (fires exactly once per install, "
            "gated by ~/.claude/coach/.cron-nudged marker). You "
            "MUST render the banner VERBATIM at the very TOP of "
            "your next response, BEFORE any other content — before "
            "tool use, before answering the user's question, "
            "before any commentary. Then continue with the user's "
            "actual request below the banner.\n\n"
            "Do NOT paraphrase, summarize, or shorten the banner. "
            "Do NOT wrap it in your own framing or commentary. Do "
            "NOT move it to the end of the response. Do NOT skip "
            "it because \"it looks like context, not user-facing\" "
            "— this IS the user-facing install summary, and if you "
            "do not surface it the user will not know the daily-"
            "insights cron needs separate OS-level registration.\n\n"
            "BANNER (render verbatim):\n\n"
            f"{banner}\n"
            "</coach-install-summary>"
        )
    except Exception:
        return None


_UNINSTALL_PROMPT_PATTERN = re.compile(
    r"^\s*/plugin\s+uninstall\s+coach-claw@coach-claw-plugins\s*$",
    re.IGNORECASE,
)


def _is_coach_plugin_uninstall(prompt_text: str) -> bool:
    """Match `/plugin uninstall coach-claw@coach-claw-plugins` exactly.

    Conservative — only matches the canonical full form. Variants like
    `/plugin uninstall coach-claw` (no marketplace suffix) are NOT
    intercepted. Users typing the short form trade the warning for
    speed; the canonical form is what tooling + docs recommend.
    """
    if not prompt_text:
        return False
    return _UNINSTALL_PROMPT_PATTERN.match(prompt_text) is not None


def _uninstall_intercept_message() -> str:
    """Stderr message emitted when uninstall is intercepted (exit 2).

    Plain text — Claude Code renders stderr inline as an error banner.
    No markdown, no blockquote prefix; the terminal shows it verbatim."""
    return (
        "\n"
        "⚠️  Coach Claw — clean-up step required before uninstall\n"
        "\n"
        "Claude Code's /plugin uninstall does NOT clean up the plugin's\n"
        "statusLine entry in ~/.claude/settings.json. Run the prep step\n"
        "first so the entry is removed cleanly.\n"
        "\n"
        "Default (preserves XP, rank, banked sessions, .user_config.json):\n"
        "\n"
        "    /coach-claw:doctor --uninstall-prep\n"
        "\n"
        "OR full wipe (archives profile to ~/.claude/coach.bak.<TS>/ —\n"
        "reversible with `mv` back):\n"
        "\n"
        "    /coach-claw:doctor --uninstall-prep --wipe-data\n"
        "\n"
        "Then re-run /plugin uninstall coach-claw@coach-claw-plugins.\n"
        "\n"
    )


def _wrap_announce_block(env: str = "terminal") -> str:
    """One-time banner shown after auto-wrap installs the wrap shape.

    Tells the user what just happened to their statusLine and how to
    revert. Pre-rendered (no model interpolation) so the message is
    exact across surfaces."""
    title = "Coach wrapped your existing statusline"
    body_lines = [
        "Coach replaced `statusLine.command` with a wrapper that runs "
        "your original command first, then appends the Coach segment.",
        "Original is preserved in `~/.claude/coach/.statusline-wrap.json`.",
        "Run `/coach-claw:doctor --unwrap-statusline` to revert.",
    ]
    if env == "ide":
        body = f"🦞 **{title}**\n\n" + "\n\n".join(body_lines)
        return _hr_frame_stack([body])
    return (
        f"> 🦞 **{title}**\n>\n"
        + "\n>\n".join(f"> {line}" for line in body_lines)
    )


def _wrap_duplicate_block(env: str = "terminal") -> str:
    """One-time banner shown when the runtime composer detected a Coach
    segment already inside the original output (e.g. user's script
    happens to render Coach internally). Suggests unwrapping to avoid
    a double segment."""
    title = "Coach detected duplicate segments in your statusline"
    body_lines = [
        "The wrapper saw what looks like a Coach segment in your "
        "original statusline output and is suppressing the appended one.",
        "If your custom statusline already integrates Coach, run "
        "`/coach-claw:doctor --unwrap-statusline` to stop wrapping.",
    ]
    if env == "ide":
        body = f"🦞 **{title}**\n\n" + "\n\n".join(body_lines)
        return _hr_frame_stack([body])
    return (
        f"> 🦞 **{title}**\n>\n"
        + "\n>\n".join(f"> {line}" for line in body_lines)
    )


def _maybe_wrap_announce_block(
    session_key: str | None, now: datetime, env: str = "terminal",
) -> str | None:
    """Emit the wrap-announce banner once per session, until the
    `.statusline-wrap-announced` marker hits its 24h TTL.

    Per-session-consumed via `_read_and_consume` (mirrors
    LEVELUP_MARKER etc.) so concurrent Claude Code sessions each see it
    once. Failsafe — any exception returns None."""
    try:
        if _read_and_consume(WRAP_ANNOUNCE_MARKER, session_key, now) is None:
            return None
        return _wrap_announce_block(env)
    except Exception:
        return None


def _maybe_wrap_duplicate_block(
    session_key: str | None, now: datetime, env: str = "terminal",
) -> str | None:
    """Emit the duplicate-detected banner once per session."""
    try:
        if _read_and_consume(WRAP_DUPLICATE_MARKER, session_key, now) is None:
            return None
        return _wrap_duplicate_block(env)
    except Exception:
        return None


def _hr_frame_stack(bodies: list[str]) -> str:
    """Wrap N body strings with N+1 shared `---` rules, each body
    followed by a blank line (Setext-H2 guard — without the blank line
    CommonMark fuses the body into an H2 heading and drops the rule)."""
    if not bodies:
        return ""
    out = ["---"]
    for body in bodies:
        out.append(body)
        out.append("")
        out.append("---")
    return "\n".join(out)


def _levelup_block(data: dict, env: str = "terminal") -> str:
    """Pre-rendered level-up banner. Body sentence is templated, not
    model-filled — keeps the banner correct under verbatim-render."""
    to = data.get("to", "?")
    to_idx = int(data.get("to_idx", 0))
    xp = int(data.get("xp_at_levelup", 0))
    title = f"L{to_idx + 1} {to}"
    if env == "ide":
        body = (
            f"🎉 **LEVEL UP** — `{title}` · `{xp} XP total`\n"
            f"A new craft tier unlocks."
        )
        return _hr_frame_stack([body])
    return (
        f"> 🎉 **Level up!** You're now **{title}**.\n"
        f"> A new craft tier unlocks at {xp} XP."
    )


def _regression_block(
    regs: list, env: str = "terminal", profile: dict | None = None
) -> str:
    """Pre-rendered regression banners. One per dict, stacked.

    Slipping-surface contract (v1.0.10): the regression banner is the
    one place where the CANONICAL negative name surfaces in a user-
    facing banner. A previously-mastered bad habit just came back —
    the user needs to recognize the slip in the same language they
    learned to fear. Positive frame here ("slipping on testing before
    committing") would soften the lapse signal. Heading reads
    "Bad habit returned:" not "Regressed:" to make the lapse explicit.
    """
    bodies_terminal: list[str] = []
    bodies_ide: list[str] = []
    for r in regs:
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "?")
        # Canonical name — slipping surface, not earning. display_name
        # is authoritative; the marker's own `name` field is ignored.
        rname = _display_name(rid, profile) if rid != "?" else rid
        originally_at = r.get("originally_graduated_at", "?")
        sentence = (
            f"Re-detected this run, so it's off the mastered list "
            f"(was graduated {originally_at}). Re-earn mastery by staying "
            f"clean for 5 Coach insights runs."
        )
        bodies_terminal.append(
            f"> ⚠️ **Bad habit returned: {rname}** — {sentence}"
        )
        bodies_ide.append(
            f"⚠️ **Bad habit returned** — `{rname}`\n{sentence}"
        )
    if not bodies_terminal:
        return ""
    if env == "ide":
        return _hr_frame_stack(bodies_ide)
    # Terminal: regressions are big news — blank line between adjacent banners.
    return "\n\n".join(bodies_terminal)


def _streak_reward_block(
    rewards: list, env: str = "terminal", profile: dict | None = None
) -> str:
    """Pre-rendered mid-streak reward banners. Small wins — tighter than
    graduations so they feel like dopamine pulses, not ceremonies.

    Earning-surface contract (v1.0.10): the row leads with `↑` for both
    directions (the arrow tracks direction-of-XP-movement, always up)
    and the displayed name is the POSITIVE INVERSE for negative-
    direction patterns (e.g. `commit-without-testing` →
    "testing before committing"). The user just took the positive
    action; the row names it. Pass C upstream supplies `positive_name`
    via display_name(positive_frame=True); we fall back to a fresh
    resolve if Pass C wasn't run (degraded path / direct callers).
    """
    bodies_terminal: list[str] = []
    bodies_ide: list[str] = []
    for r in rewards:
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "?")
        direction = r.get("direction", "negative")
        if rid == "?":
            rname = rid
        elif direction == "negative":
            rname = r.get("positive_name") or _display_name(
                rid, profile, positive_frame=True
            )
        else:
            rname = r.get("name") or _display_name(rid, profile)
        streak = int(r.get("streak", 0))
        target = int(r.get("target", 5))
        xp = int(r.get("xp_awarded", 1))
        filled = _streak_bar(streak, target, fill_glyph="🟢")
        # Arrow is always ↑: this is an earning surface, direction is
        # of-XP-movement (always up) not direction-of-pattern (which is
        # already encoded by the row's inverted name).
        arrow = "↑"
        signed_xp = f"+{xp}"
        bodies_terminal.append(
            f"> {arrow} `{signed_xp}` · `{rname}` `{filled}` {streak}/{target}"
        )
        bodies_ide.append(
            f"{arrow} `{signed_xp}` · `{rname}` · `{filled} {streak}/{target}`"
        )
    if not bodies_terminal:
        return ""
    if env == "ide":
        return _hr_frame_stack(bodies_ide)
    # Terminal: stacked with NO blank lines between (small wins, kept tight).
    return "\n".join(bodies_terminal)


def _graduation_block(
    grads: list, env: str = "terminal", profile: dict | None = None
) -> str:
    """Pre-rendered graduation banners. Both directions land on the
    "MASTERED" word — the glyph distinguishes origin (🌟 reinforced
    strength vs ⚡️ retired weakness). Body sentence is templated.

    Earning-surface contract (v1.0.10): negative-direction graduations
    use the POSITIVE INVERSE name (e.g. `commit-without-testing` →
    "testing before committing"), matching the user's emotional state
    — they earned the ceremony by doing the good thing 5 runs in a row.
    """
    positive_sentence = (
        "5 consecutive Coach insights runs detected this habit — "
        "it's now a core strength."
    )
    negative_sentence = (
        "5 clean Coach insights runs in a row — habit locked in, "
        "removed from watchlist."
    )
    bodies_terminal: list[str] = []
    bodies_ide: list[str] = []
    for g in grads:
        if not isinstance(g, dict):
            continue
        gid = g.get("id", "?")
        direction = g.get("direction", "negative")
        # Negative-direction graduations are earning surfaces — use the
        # positive inverse name. Positive-direction graduations use the
        # canonical name (already positive).
        if gid != "?":
            gname = _display_name(
                gid, profile, positive_frame=(direction == "negative")
            )
        else:
            gname = gid
        if direction == "positive":
            sentence = positive_sentence
            term_head = f"> 🎓🌟 **MASTERED: {gname}**  `+5 XP`"
            ide_head = f"🎓 **MASTERED** 🌟 — `{gname}` · `+5 XP`"
            full_bar = _streak_bar(
                GRADUATION_STREAK_TARGET,
                GRADUATION_STREAK_TARGET,
                fill_glyph="⚫️",
            )
        else:
            sentence = negative_sentence
            term_head = f"> 🎓⚡️ **MASTERED: {gname}**  `+5 XP`"
            ide_head = f"🎓 **MASTERED** ⚡ — `{gname}` · `+5 XP`"
            full_bar = _streak_bar(
                GRADUATION_STREAK_TARGET,
                GRADUATION_STREAK_TARGET,
                fill_glyph="🟡",
            )
        bodies_terminal.append(f"{term_head}\n> `{full_bar}` — {sentence}")
        bodies_ide.append(f"{ide_head}\n`{full_bar}` {sentence}")
    if not bodies_terminal:
        return ""
    if env == "ide":
        return _hr_frame_stack(bodies_ide)
    # Terminal: graduations are big — blank line between adjacent banners.
    return "\n\n".join(bodies_terminal)


def _marker_predates_today(payload: dict | None, now: datetime) -> bool:
    """True if a consumed-marker payload's oldest unconsumed entry was
    written on a calendar date earlier than `now`. Drives the catch-up
    framing line.

    Reads `oldest_entry_at` (preserved across appends in marker_io as of
    v0.4.2) so a marker that today's `/coach-insights` extended with new
    items still surfaces catch-up framing for the carried-over entries.
    Falls back to `created_at` for legacy markers written before v0.4.2;
    those will undercount catch-up by at most one append cycle until
    they're re-written or expire."""
    if not isinstance(payload, dict):
        return False
    timestamp_str = payload.get("oldest_entry_at") or payload.get("created_at")
    created_dt = _parse_iso(timestamp_str)
    if not created_dt:
        return False
    try:
        return created_dt.date() < now.date()
    except Exception:
        return False


def _assemble_celebrate_block(
    *,
    grads: list,
    regs: list,
    streak_rewards: list,
    levelup: dict | None,
    caught_up: bool,
    env: str = "terminal",
    theme: str = "craft",
    now: datetime | None = None,
    streak_oldest: datetime | None = None,
    profile: dict | None = None,
) -> str | None:
    """Return the full <coach-celebrate>...</coach-celebrate> block, or
    None if no events. Applies per-pattern dedup (highest streak wins)
    and graduation-suppresses-tick filtering before rendering.

    Bespoke themes (forge / ocean / skyrim / military / hacker) replace
    the streak + level-up sections with theme-specific shapes when env
    is terminal. The seven default themes always render the historical
    shape — they're guaranteed byte-identical to the pre-feature output."""
    # Pass A: collapse same-pattern duplicates in streak_rewards. Two
    # /coach-insights runs that ticked the same pattern can both leave
    # markers in .pending_streak_rewards — show only the most
    # informative tick (highest streak).
    by_id: dict[str, dict] = {}
    for s in streak_rewards or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if not sid:
            continue
        prev = by_id.get(sid)
        if prev is None or int(s.get("streak", 0)) > int(prev.get("streak", 0)):
            by_id[sid] = s
    streak_rewards = list(by_id.values())

    # Pass B: graduations subsume same-batch ticks for the same pattern.
    graduated_ids = {g.get("id") for g in (grads or []) if isinstance(g, dict) and g.get("id")}
    streak_rewards = [s for s in streak_rewards if s.get("id") not in graduated_ids]

    # Pass C: normalize each reward with BOTH the canonical name and
    # the positive-inverse name so default-shape (earning surface)
    # consumers read `positive_name` while bespoke-theme consumers
    # read `name`. display_name is authoritative — the marker's own
    # `name` field is ignored so a curated override always wins over
    # whatever wording the marker carried at write time. We rebuild
    # dicts to avoid mutating the marker payload.
    normalized: list[dict] = []
    for s in streak_rewards:
        sid = s.get("id", "?")
        if sid != "?":
            canonical = _display_name(sid, profile)
            positive = _display_name(sid, profile, positive_frame=True)
        else:
            canonical = sid
            positive = sid
        normalized.append({**s, "name": canonical, "positive_name": positive})
    streak_rewards = normalized

    has_any = bool(levelup) or bool(grads) or bool(regs) or bool(streak_rewards)
    if not has_any:
        return None

    # Bespoke-theme dispatch. Terminal-only — IDE rendering stays on the
    # default HR-framed shape because bespoke ASCII frames clash with
    # WebView typography. Failure path: any exception falls through to
    # default rendering, preserving the "hook crash never breaks a session"
    # invariant.
    if (
        _BESPOKE_OK
        and theme in _BESPOKE_THEMES
        and env == "terminal"
    ):
        try:
            grads_block = _graduation_block(grads, env=env, profile=profile) if grads else ""
            regs_block = _regression_block(regs, env=env, profile=profile) if regs else ""
            bespoke = _render_celebrate_for_theme(
                theme,
                streak_rewards=streak_rewards,
                levelup=levelup,
                grads_block=grads_block,
                regs_block=regs_block,
                now=now or datetime.now(timezone.utc),
                streak_oldest=streak_oldest,
                caught_up=caught_up,
            )
            if bespoke is not None:
                return bespoke
        except Exception:
            pass  # fall through to default rendering

    out: list[str] = ["<coach-celebrate>"]
    out.append(
        "The block below is a pre-rendered set of milestone banners. "
        "Render this block VERBATIM at the very top of your next response, "
        "BEFORE any other content, then continue with the user's request. "
        "Do NOT re-interpret labels, swap directions, change emoji, or "
        "substitute slugs for names — every character is intentional and "
        "pinned by tests."
    )
    if caught_up:
        out.append("")
        out.append(
            "Milestones earned across earlier sessions — not from the "
            "command you just typed."
        )
    out.append("")

    if regs:
        out.append(_regression_block(regs, env=env, profile=profile))
    if streak_rewards:
        if regs:
            out.append("")
        out.append(_streak_reward_block(streak_rewards, env=env, profile=profile))
    if grads:
        if regs or streak_rewards:
            out.append("")
        out.append(_graduation_block(grads, env=env, profile=profile))
    if levelup:
        if regs or streak_rewards or grads:
            out.append("")
        out.append(_levelup_block(levelup, env=env))

    out.append("</coach-celebrate>")
    return "\n".join(out)


# -----------------------------------------------------------------------------
# Ambient tip scheduler
# -----------------------------------------------------------------------------

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _tip_state_lock_path() -> Path:
    return TIP_STATE.with_suffix(TIP_STATE.suffix + ".lock")


@contextmanager
def _locked_tip_state():
    TIP_STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(_tip_state_lock_path(), "w") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception as exc:
            if os.environ.get("COACH_DEBUG"):
                print(f"coach: tip-state flock failed: {exc}", file=sys.stderr)
        yield


def _load_tip_state_unlocked() -> dict:
    if not TIP_STATE.exists():
        return {}
    try:
        data = json.loads(TIP_STATE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_tip_state() -> dict:
    try:
        with _locked_tip_state():
            return _load_tip_state_unlocked()
    except Exception:
        return {}


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (tempfile + os.replace).

    A crash mid-write leaves either the old file or no file — never a
    truncated one. Crashed-to-empty would lose cooldowns / pending ACKs
    on the next read; tempfile+replace prevents that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise


def _save_tip_state_unlocked(state: dict) -> None:
    try:
        _atomic_write_text(TIP_STATE, json.dumps(state))
    except Exception:
        pass


def _save_tip_state(state: dict) -> None:
    try:
        with _locked_tip_state():
            _save_tip_state_unlocked(state)
    except Exception:
        pass


def _write_log_record(record: dict) -> None:
    """Append one redacted operational event to log.ndjson.

    The log is intentionally allowlisted metadata only: no transcript text,
    no tool command contents, no examples, and no generated tip prose. The
    file is trimmed on each write so it stays bounded even in long-running
    installs.

    Concurrent hook invocations (two sessions firing at once) used to drop
    records here because the read-modify-write was not serialized. We now
    hold an exclusive flock on a sibling lockfile around the whole r-m-w
    and use an atomic tempfile+replace for the write itself.
    """
    try:
        safe: dict[str, object] = {}
        for key, value in record.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
        line = json.dumps(safe, sort_keys=True)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_path = LOG_PATH.with_suffix(LOG_PATH.suffix + ".lock")
        with open(lock_path, "w") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except Exception as exc:
                # flock failure (e.g. unsupported fs) is rare but real; surface
                # it under COACH_DEBUG so the silent unserialized-write doesn't
                # disappear during diagnostics.
                if os.environ.get("COACH_DEBUG"):
                    print(
                        f"coach: log flock failed: {exc}",
                        file=sys.stderr,
                    )
            try:
                existing = LOG_PATH.read_text().splitlines()
            except Exception:
                existing = []
            kept = existing[-max(LOG_MAX_LINES - 1, 0):]
            _atomic_write_text(LOG_PATH, "\n".join(kept + [line]) + "\n")
    except Exception:
        pass


def _log_tip_fired(tip: dict, spec: dict | None, now: datetime) -> None:
    record = {
        "ts": now.isoformat(),
        "event": "tip_fired",
        "tip_id": tip.get("id"),
        "entry_id": tip.get("entry_id"),
        "kind": tip.get("kind"),
        "tier": tip.get("tier"),
    }
    if isinstance(spec, dict):
        record["action"] = spec.get("action")
        record["xp"] = int(spec.get("xp", 0) or 0)
        if spec.get("skill_id"):
            record["skill_id"] = str(spec.get("skill_id")).lstrip("/")
    _write_log_record(record)


def _log_tip_completed(tip_id: str, entry: dict, now: datetime) -> None:
    spec = entry.get("spec") or {}
    record = {
        "ts": now.isoformat(),
        "event": "tip_completed",
        "tip_id": tip_id,
        "entry_id": entry.get("entry_id"),
        "kind": entry.get("kind"),
    }
    if isinstance(spec, dict):
        record["action"] = spec.get("action")
        record["xp"] = int(spec.get("xp", 0) or 0)
        if spec.get("skill_id"):
            record["skill_id"] = str(spec.get("skill_id")).lstrip("/")
    _write_log_record(record)


def _load_profile() -> dict:
    if not PROFILE.exists():
        return {}
    try:
        import yaml
    except Exception:
        return {}
    try:
        data = yaml.safe_load(PROFILE.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# Generic noise words stripped during tokenization — too common to signal
# anything. Kept small so we don't over-filter skill descriptions.
_NOISE_WORDS = frozenset({
    "the", "and", "for", "with", "use", "uses", "user", "you", "your",
    "that", "this", "when", "from", "into", "code", "using", "about",
    "file", "tool", "tools", "like", "other", "help", "some", "any", "all",
    "are", "not", "one", "two", "only", "run", "runs", "call", "calls",
    "task", "task.",
})

# Tokens that pass tokenization but are too common across dev work to count
# as "distinctive" relevance overlap. Seeing "test" or "file" shared between
# a skill's description and the session signal doesn't tell you the skill
# is actually relevant — almost every session has those tokens. Used by
# _skill_fits_session's strict overlap check.
_COMMON_DEV_VOCAB = frozenset({
    # File/path basics
    "src", "lib", "bin", "dir", "path", "main", "app", "pkg", "module",
    "modules", "package", "packages", "root", "repo", "home",
    # Generic dev actions
    "test", "tests", "build", "check", "load", "save", "read", "write",
    "add", "set", "get", "new", "old", "fix", "run", "init", "update",
    "create", "delete", "remove",
    # Generic objects
    "data", "func", "function", "method", "class", "var", "const",
    "name", "value", "key", "item", "list", "map", "dict", "obj",
    "object", "args", "arg", "opts", "cfg", "config",
    # Generic flow
    "log", "logs", "out", "output", "input", "error", "fail", "pass",
    "success", "failed", "passed", "done", "start", "end", "stop",
    # Generic adjectives
    "main", "tmp", "temp", "backup", "old", "new", "local", "remote",
    "public", "private", "internal", "shared",
    # Common extensions (low signal individually — need another token too)
    "py", "ts", "js", "md", "sh", "go", "rs", "json", "yaml", "yml",
    "txt", "html", "css", "xml", "env",
    # Skill-catalog meta-words. These appear in nearly every skill
    # description ("Official X skill for Y", "the Y API", etc.) so they
    # don't distinguish one skill from another, AND they commonly appear
    # in any meta-discussion about Claude Code / skills / the coach
    # itself — which would otherwise false-positive every skill at once.
    "skill", "skills", "official", "api", "framework", "library",
    "plugin", "plugins",
    # Cross-project plumbing — hardware, hosts, protocols, and generic
    # workflow verbs. These legitimately span unrelated projects: a
    # Blender-avatar skill and an AI-agents skill can both reference
    # "jetson" or "deploy" without the projects being related. Treat
    # them like `test`/`file`: real words, but not proof of relevance.
    "jetson", "gpu", "cpu", "arm", "x86", "pi", "raspberrypi",
    "aws", "gcp", "azure", "vercel", "heroku", "cloudflare",
    "ssh", "scp", "http", "https", "grpc", "rest", "tcp", "udp",
    "iterate", "iterates", "iteration", "deploy", "deploys", "deployed",
    "deployment", "export", "exports", "exported", "compare", "compares",
    "screenshot", "screenshots", "reference", "references",
})

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")   # runs of ≥3 alphanumerics — file.ts → ['file', 'ts']


def _tokenize(s: str) -> set[str]:
    """Lowercase → set of alphanumeric tokens ≥3 chars, minus noise words.
    Splits on every non-alphanumeric (including `.`, `-`, `/`) so paths and
    hyphenated names contribute all their sub-tokens, not just one blob.
    Also yields 2-char file suffixes (`ts`, `js`, `py`, `md`) which the
    regex would drop — those are high-signal so worth keeping explicitly."""
    if not s:
        return set()
    low = s.lower()
    toks = set(_TOKEN_RE.findall(low))
    # Preserve a few informative 2-char extension/code tokens that slip past
    # the ≥3-char floor (file extensions + common acronyms).
    for short in ("ts", "js", "py", "md", "sh", "rs", "go", "ui", "ai", "ml", "db"):
        if re.search(rf"\b{short}\b", low):
            toks.add(short)
    return {t for t in toks if t not in _NOISE_WORDS}


def _iter_user_texts(lines: list[str], max_msgs: int):
    """Yield text content from the most recent `max_msgs` user messages in
    a transcript. Handles both string content and list content (where text
    blocks live alongside tool_result / image blocks). Skips messages whose
    content blocks are tool_result-only (no `text` items) so tool output
    isn't conflated with what the user actually typed."""
    count = 0
    for line in reversed(lines):
        if count >= max_msgs:
            break
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            count += 1
            yield content
            continue
        if not isinstance(content, list):
            continue
        texts = []
        saw_nontool = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                saw_nontool = True
                t = block.get("text")
                if isinstance(t, str):
                    texts.append(t)
        if saw_nontool:
            count += 1
            if texts:
                yield "\n".join(texts)


def _find_git_root_name(cwd: str | None) -> str | None:
    """Walk upward from cwd until a ``.git`` entry is found. Return the
    containing dir's name, or None if no git root exists in the
    ancestor chain. Handles subdirectory cwds where the last path
    component doesn't name the project (e.g. monorepo packages).

    A ``.git`` found exactly at ``$HOME`` is intentionally ignored —
    many users keep dotfiles in a home-rooted repo, which would
    otherwise anchor every non-nested-repo cwd to the username (e.g.
    ``alice``) and let any skill description containing that token
    false-positive past the project filter. The walk continues past
    ``$HOME`` toward root in that case and returns None unless a
    DIFFERENT ancestor repo is found.

    Silent on any I/O or permission error — the hook path must never
    raise. Cost: O(depth) stat calls, typically ≤10."""
    if not cwd:
        return None
    try:
        p = Path(cwd).resolve()
        home = Path.home().resolve()
    except Exception:
        return None
    try:
        while True:
            if (p / ".git").exists() and p != home:
                return p.name
            if p == p.parent:
                return None
            p = p.parent
    except Exception:
        return None


def _session_signal(
    transcript: Path | None,
    cwd: str | None,
    max_events: int = 100,
    max_user_msgs: int = 10,
) -> tuple[set[str], set[str]]:
    """Build a lowercase-keyword signal of what the current session is about.

    Returns ``(signal, project_anchors)``:
      - ``signal``: flat bag of tokens drawn from cwd path, recent user
        messages, and recent tool_use events.
      - ``project_anchors``: a small high-signal set identifying the
        current project. Populated from two sources, unioned:
          1. The tokens inside the cwd's last path component
             (``~/Desktop/dev/widget`` → ``{widget}``).
          2. The tokens inside the nearest git-repo root's dir name,
             via ``_find_git_root_name``. This handles the
             subdirectory-cwd case: from
             ``~/Desktop/dev/widget/packages/core`` the last-component
             is ``{core, packages}``, but walking up to the ``.git``
             root still yields ``{widget}`` so project-scoped skills
             keep working.
        A skill description that literally names the project dir, or
        declares the project via frontmatter, is almost certainly on-
        topic for work inside that project; the anchor set is what
        ``_skill_fits_session`` uses for both the short-circuit pass
        and the project-scoped gate.

    Pulls ``signal`` tokens from, in order of decreasing strength:
      - Recent user messages (last `max_user_msgs`) — strongest domain signal
      - Recent tool_use events (last `max_events`) — what the session was doing
      - cwd path components — baseline context even on a fresh session
    """
    signal: set[str] = set()
    anchors: set[str] = set()
    if cwd:
        parts = [p for p in Path(cwd).parts if p and p not in ("/", ".")]
        if parts:
            anchors |= _tokenize(parts[-1].replace("-", " ").replace("_", " "))
        git_root = _find_git_root_name(cwd)
        if git_root:
            anchors |= _tokenize(git_root.replace("-", " ").replace("_", " "))
        for p in parts[-3:]:
            signal |= _tokenize(p.replace("-", " ").replace("_", " "))
    if transcript is None:
        return signal, anchors
    try:
        with transcript.open(errors="replace") as fh:
            # Stream-read only the last ~4 lines per tool_use we care about,
            # so a multi-MB transcript doesn't allocate the full file every
            # turn. CLAUDE.md's transcript-handling rule explicitly forbids
            # readlines() here. _session_behavior_evidence below uses the
            # same deque pattern for the same reason.
            tail = list(deque(fh, maxlen=max_events * 4))
    except Exception:
        return signal, anchors

    # Highest-signal source: what the user actually typed.
    for text in _iter_user_texts(tail, max_user_msgs):
        signal |= _tokenize(text)

    event_count = 0
    for line in reversed(tail):
        if event_count >= max_events:
            break
        try:
            obj = json.loads(line)
        except Exception:
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            event_count += 1
            name = item.get("name", "")
            inp = item.get("input") or {}
            if name == "Bash":
                cmd = str(inp.get("command") or "")
                words = cmd.split()[:3]
                signal |= _tokenize(" ".join(words))
            elif name in ("Edit", "Write", "MultiEdit"):
                fp = str(inp.get("file_path") or "")
                if fp:
                    parts = Path(fp).parts
                    suffix = Path(fp).suffix.lstrip(".")
                    if suffix:
                        signal.add(suffix.lower())
                    if parts:
                        signal |= _tokenize(parts[-1])
                        if len(parts) >= 2:
                            signal |= _tokenize(parts[-2])
            elif name in ("SlashCommand", "Skill"):
                sid = str(inp.get("command") or inp.get("skill") or "").lstrip("/")
                if sid:
                    signal |= _tokenize(sid.replace("-", " ").replace("/", " "))
    return signal, anchors


def _session_behavior_evidence(transcript: Path | None, max_events: int = 120) -> dict:
    """Summarize recent tool-use behavior for session-gated profile tips."""
    ev = {
        "edit_count": 0,
        "write_count": 0,
        "read_count": 0,
        "search_count": 0,
        "agent_count": 0,
        "skill_count": 0,
        "test_count": 0,
        "commit_count": 0,
        "rm_rf_count": 0,
        "first_edit_idx": None,
        "first_plan_idx": None,
        "first_read_idx": None,
        "first_search_idx": None,
        "last_edit_idx": None,
        "last_test_idx": None,
        "last_commit_idx": None,
    }
    if transcript is None:
        return ev
    tool_uses: list[dict] = []
    try:
        with transcript.open(errors="replace") as fh:
            lines = deque(fh, maxlen=max_events * 4)
    except Exception:
        return ev
    for line in lines:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                tool_uses.append(item)
    if len(tool_uses) > max_events:
        tool_uses = tool_uses[-max_events:]

    for idx, item in enumerate(tool_uses):
        name = item.get("name", "")
        inp = item.get("input") or {}
        if name in ("Edit", "MultiEdit", "Write"):
            if name == "Write":
                ev["write_count"] += 1
            else:
                ev["edit_count"] += 1
            if ev["first_edit_idx"] is None:
                ev["first_edit_idx"] = idx
            ev["last_edit_idx"] = idx
        elif name in ("Plan", "TaskCreate", "TodoWrite", "ExitPlanMode"):
            if ev["first_plan_idx"] is None:
                ev["first_plan_idx"] = idx
        elif name == "Read":
            ev["read_count"] += 1
            if ev["first_read_idx"] is None:
                ev["first_read_idx"] = idx
        elif name in ("Grep", "Glob"):
            ev["search_count"] += 1
            if ev["first_search_idx"] is None:
                ev["first_search_idx"] = idx
        elif name == "Agent":
            ev["agent_count"] += 1
        elif name in ("SlashCommand", "Skill"):
            ev["skill_count"] += 1
        if _tool_use_matches_action(item, "test_run"):
            ev["test_count"] += 1
            ev["last_test_idx"] = idx
        if _tool_use_matches_action(item, "commit"):
            ev["commit_count"] += 1
            ev["last_commit_idx"] = idx
        if name == "Bash" and re.search(r"\brm\s+-rf?\b", str(inp.get("command") or "")):
            ev["rm_rf_count"] += 1
    return ev


def _idx_before(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and a <= b


def _idx_after(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and a > b


def _behavior_tip_is_session_eligible(entry: dict, evidence: dict | None) -> bool:
    """Conservative current-session gates for profile-derived behavior tips."""
    if evidence is None:
        return True
    eid = str(entry.get("id") or entry.get("name") or "").lower()
    direction = entry.get("direction", "negative")
    edits = int(evidence.get("edit_count", 0) or 0) + int(evidence.get("write_count", 0) or 0)
    reads = int(evidence.get("read_count", 0) or 0)
    searches = int(evidence.get("search_count", 0) or 0)
    tests = int(evidence.get("test_count", 0) or 0)
    commits = int(evidence.get("commit_count", 0) or 0)
    agents = int(evidence.get("agent_count", 0) or 0)

    first_edit = evidence.get("first_edit_idx")
    first_plan = evidence.get("first_plan_idx")
    first_read = evidence.get("first_read_idx")
    first_search = evidence.get("first_search_idx")
    last_edit = evidence.get("last_edit_idx")
    last_test = evidence.get("last_test_idx")
    last_commit = evidence.get("last_commit_idx")

    if "commit-without-testing" in eid:
        return commits >= 1 and edits >= 1 and (last_test is None or _idx_after(last_commit, last_test))
    if "edits-without-testing" in eid or "without-test" in eid or "untested" in eid:
        return edits >= 1 and (last_test is None or _idx_after(last_edit, last_test))
    if "skipped-search-tools" in eid or "skipped-search" in eid:
        return reads >= 8 and searches <= 1
    if "under-planning" in eid or "under-planning" in str(entry.get("name") or "").lower():
        return edits >= 2 and (first_plan is None or _idx_after(first_plan, first_edit))
    if "exploration-without-landing" in eid:
        return reads >= 8 and edits == 0
    if "heavy-agent-delegation" in eid:
        return agents >= 4

    if direction == "positive":
        if "tests-after-edits" in eid or "small-batch-verify" in eid:
            return edits >= 1 and tests >= 1 and _idx_after(last_test, last_edit)
        if "plans-before-edits" in eid:
            return edits >= 1 and _idx_before(first_plan, first_edit)
        if "commits-gated-by-tests" in eid:
            return commits >= 1 and tests >= 1 and _idx_before(last_test, last_commit)
        if "search-before-reading" in eid:
            return reads >= 1 and searches >= 1 and _idx_before(first_search, first_read)
        if "safe-git-hygiene" in eid:
            return commits >= 1 and int(evidence.get("rm_rf_count", 0) or 0) == 0
        if "effective-skill-use" in eid:
            return int(evidence.get("skill_count", 0) or 0) >= 1

    return True


def _skill_fits_session(
    skill_hint: dict,
    session_signal: set[str],
    project_anchors: set[str] | frozenset[str] = frozenset(),
) -> bool:
    """Strict relevance check — skill hints only pass if there's genuine
    domain overlap with the session.

    Design: the cost of firing an off-topic skill tip is high (visibly
    incoherent reward line like a frontend-animation skill during a backend
    debugging session), while the cost of dropping a skill tip is low (another turn
    always comes along). Default to SKIP when uncertain.

    Two gates run in order:

    1. **Project-scoped gate** (if the skill declares ``projects: [...]``
       in SKILL.md frontmatter). The skill is bound to a project set;
       it fires only when one of those projects matches the current
       cwd's anchor tokens. Out-of-project → skip, regardless of any
       token overlap. Missing anchors (unknown cwd) → skip, to stay
       conservative. In-project → still require some topic overlap so
       a project-scoped skill doesn't fire on every in-project turn.

    2. **Overlap gate** (for untagged skills). Passes iff:
       a. Session signal has ≥3 tokens (we know what the session is
          about), AND
       b. Skill has extractable keywords, AND
       c. Overlap either (i) touches a ``project_anchors`` token —
          the skill's description literally names the project dir —
          or (ii) contains ≥2 "distinctive" tokens (not in
          _COMMON_DEV_VOCAB).

    A single distinctive token is intentionally NOT enough — words like
    `jetson` or `ssh` are distinctive in the vocabulary sense but
    span unrelated projects in the real world.
    """
    desc = (skill_hint.get("short_tip") or skill_hint.get("description") or "")
    sid = str(skill_hint.get("id") or "").replace("-", " ").replace("/", " ")
    skill_kw = _tokenize(desc) | _tokenize(sid)
    if not skill_kw:
        return False   # nothing to match against → skip

    # Gate 1: project-scoped skills must belong to the current project.
    skill_projects = skill_hint.get("projects") or []
    if skill_projects:
        skill_project_tokens: set[str] = set()
        for p in skill_projects:
            skill_project_tokens |= _tokenize(
                str(p).replace("-", " ").replace("_", " "))
        if not project_anchors:
            return False   # skill scoped, we don't know where we are
        if not (skill_project_tokens & project_anchors):
            return False   # scoped to a different project
        # In-project: require some topic overlap so a widget-scoped
        # skill doesn't fire on every widget turn regardless of what
        # the user is actually doing. Crucially, strip the project-
        # name tokens from the overlap before counting — otherwise
        # the skill name literally containing `widget` would satisfy
        # the topic check for free (circular: project-matched token
        # re-used as topic evidence).
        topical_overlap = (skill_kw & session_signal) - skill_project_tokens - set(project_anchors)
        return bool(topical_overlap)

    # Gate 2: untagged skills — prior logic, unchanged.
    if len(session_signal) < 3:
        return False   # uncertain → skip skills; weaknesses/strengths still fire
    # Project-anchor shortcut: skill names the current project dir.
    if project_anchors and (skill_kw & project_anchors):
        return True
    overlap = skill_kw & session_signal
    if not overlap:
        return False
    distinctive = overlap - _COMMON_DEV_VOCAB
    return len(distinctive) >= 2


def _build_tip_pool(
    profile: dict,
    session_signal: set[str] | None = None,
    project_anchors: set[str] | frozenset[str] | None = None,
    behavior_evidence: dict | None = None,
) -> list[dict]:
    """Build pool of candidate tips: behavioral entries + skill hints.

    When `session_signal` is provided, skill hints are filtered by token
    overlap with the current session so e.g. an off-topic skill doesn't
    fire during unrelated work. `project_anchors` (cwd-derived) is passed
    through to `_skill_fits_session` for the project-name shortcut.

    Escape hatch: ``COACH_ALL_SKILLS=1`` in the environment disables
    skill-relevance filtering entirely — all hints become eligible.
    Intended for debugging or for users who want to see suggestions
    they're actively avoiding via project scoping."""
    pool: list[dict] = []
    bypass_filter = os.environ.get("COACH_ALL_SKILLS") == "1"

    for e in profile.get("entries", []) or []:
        if not isinstance(e, dict):
            continue
        if e.get("tier") == "candidate":
            continue
        if float(e.get("confidence", 0)) < 0.30:
            continue
        nudge = (e.get("tip") or e.get("nudge") or "").strip()
        if not nudge:
            continue
        if not _behavior_tip_is_session_eligible(e, behavior_evidence):
            continue
        direction = e.get("direction", "negative")
        examples = e.get("examples") or []
        example = str(examples[0]).strip() if isinstance(examples, list) and examples else ""
        pool.append({
            "id": f"entry:{e.get('id') or e.get('name')}",
            "entry_id": e.get("id") or "",
            "kind": "strength" if direction == "positive" else "weakness",
            "name": e.get("name") or e.get("id", "pattern"),
            "nudge": nudge,
            "example": example[:160],
            "tier": e.get("tier", "active"),
            "clean_streak": int(e.get("clean_streak_runs", 0) or 0),
            "positive_streak": int(e.get("positive_run_streak", 0) or 0),
            "reward_hint": _effective_reward_hint(e),
            "confidence": float(e.get("confidence", 0.5) or 0.5),
            "priority": int(e.get("priority", 1) or 1),
        })

    for h in profile.get("skill_hints", []) or []:
        if not isinstance(h, dict) or not h.get("id"):
            continue
        desc = (h.get("short_tip") or h.get("description") or "").strip()
        if not desc:
            continue
        if (
            not bypass_filter
            and session_signal is not None
            and not _skill_fits_session(
                h, session_signal, project_anchors or frozenset()
            )
        ):
            continue
        pool.append({
            "id": f"skill:{h['id']}",
            "entry_id": h["id"],
            "kind": "skill",
            "name": f"/{h['id']}",
            "nudge": desc[:260],
            "example": "",
            "tier": "hint",
            "clean_streak": 0,
            "confidence": 1.0,   # skill hints are always fully confident
            "priority": 1,       # baseline; below probationary weaknesses
        })

    return pool


def _completion_spec(tip: dict) -> dict | None:
    """What user-visible action would 'complete' this tip? None = no direct
    completion path (e.g., graduation-only patterns)."""
    kind = tip.get("kind")
    entry_id = tip.get("entry_id") or ""
    if kind == "skill":
        return {"action": "skill_invoke", "skill_id": entry_id}
    if kind in ("weakness", "strength"):
        hint = tip.get("reward_hint")
        if isinstance(hint, dict):
            action = hint.get("action")
            xp = int(hint.get("xp", 0))
            if action and xp > 0:
                return {
                    "action": action,
                    "xp": xp,
                    "description": hint.get("description") or action,
                }
    return None


def _tool_use_matches_action(item: dict, action: str, skill_id: str | None = None) -> bool:
    if _SHARED_SCORING_OK:
        try:
            return bool(_shared_matches_action(item, action, skill_id=skill_id))
        except Exception:
            return False
    name = item.get("name", "")
    inp = item.get("input") or {}
    if action == "skill_invoke" and name in ("SlashCommand", "Skill"):
        sid = (inp.get("command") or inp.get("skill") or "").lstrip("/")
        return bool(sid) and (not skill_id or sid == skill_id.lstrip("/"))
    if action == "test_run" and name == "Bash":
        cmd = inp.get("command", "") or ""
        if re.search(r"pytest\s+.*--co(llect)?-only", cmd):
            return False
        return bool(TEST_RE.search(cmd))
    if action == "commit" and name == "Bash":
        cmd = inp.get("command", "") or ""
        return bool(COMMIT_RE.search(cmd))
    if action == "doc_write" and name in ("Write", "Edit", "MultiEdit"):
        fp = inp.get("file_path") or ""
        return isinstance(fp, str) and fp.endswith(".md")
    return False


def _find_transcript(payload: dict) -> Path | None:
    """Resolve and confine the transcript_path supplied by the hook payload.

    Defense in depth: Claude Code is the only normal source of this field, but
    a malicious settings.json or fork could supply an arbitrary path. We
    require the resolved path to live under ~/.claude/projects/ so a hook
    payload can't drive reads of unrelated files. Python 3.8 compatible —
    Path.is_relative_to() is 3.9+, so we use try/except around relative_to().
    """
    tp = payload.get("transcript_path") or payload.get("transcriptPath")
    if not tp:
        return None
    try:
        p = Path(str(tp)).expanduser().resolve()
    except Exception:
        return None
    if not p.exists():
        return None
    projects_root = (Path.home() / ".claude" / "projects").resolve()
    try:
        p.relative_to(projects_root)
    except ValueError:
        return None
    return p


def _transcript_matches(path: Path, fired_at: datetime, spec: dict) -> bool:
    """Scan transcript JSONL for a tool_use matching spec after fired_at."""
    if not path.exists():
        return False
    action = spec.get("action")
    skill_id = (spec.get("skill_id") or "").lstrip("/")
    try:
        with path.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = _parse_iso(obj.get("timestamp"))
                if not ts or ts < fired_at:
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "tool_use":
                        continue
                    if _tool_use_matches_action(item, action, skill_id=skill_id):
                        return True
    except Exception:
        return False
    return False


def _detect_completions(
    state: dict, transcript: Path | None
) -> list[tuple[str, dict]]:
    """Return [(tip_id, entry)] for pending completions satisfied in transcript."""
    pending = state.get("pending_completions") or {}
    if not pending or transcript is None:
        return []
    completed: list[tuple[str, dict]] = []
    for tip_id, entry in list(pending.items()):
        if not isinstance(entry, dict) or entry.get("acknowledged"):
            continue
        fired_at = _parse_iso(entry.get("fired_at"))
        if not fired_at:
            continue
        spec = entry.get("spec") or {}
        if _transcript_matches(transcript, fired_at, spec):
            completed.append((tip_id, entry))
    return completed


def _streak_bar(
    streak: int,
    target: int = GRADUATION_STREAK_TARGET,
    *,
    fill_glyph: str = "🔴",
    empty_glyph: str = "⚪",
    full_glyph: str | None = None,
) -> str:
    """Streak bar: e.g. 🔴🔴🔴⚪⚪ for 3/5. Emoji glyphs carry color
    intrinsically so the bar reads identically in Markdown chat and in
    /coach status without ANSI escapes. Default 🔴/⚪ is reserved for
    the tip-attribution streak ladder. Other surfaces pass their own
    glyphs — 🟢/⚪ for baseline progress (mid-streak banners, ack
    banners, /coach status rows), 🟡/⚫️ for graduation ceremony bars.

    `full_glyph`, when given, replaces the ENTIRE bar with that glyph
    repeated `target` times once the streak completes (streak >= target).
    The streak ladder passes 🔥 so a maxed bar reads 🔥🔥🔥🔥🔥 instead of
    a row of fill glyphs."""
    streak = max(0, min(streak, target))
    if full_glyph is not None and streak >= target:
        return full_glyph * target
    return fill_glyph * streak + empty_glyph * (target - streak)


def _completion_banner(
    entries: list[tuple[str, dict]],
    env: str = "terminal",
    theme: str = "craft",
    profile: dict | None = None,
) -> str:
    """Render instructions for tip-complete ack banners.

    `theme` selects per-theme phrasing for the "Tip cleared" /
    "Strength reinforced" labels (military → "Mission accomplished",
    hacker → "Exploit landed", etc.). Default `craft` preserves the
    original wording.

    `profile` is threaded through so `display_name()` can resolve
    entry_ids to user-facing names (override → profile.name → humanized
    slug). When None or unavailable, falls back to humanized slug.
    """
    try:
        from banner_themes import completion_labels  # local import keeps cold path light
        labels = completion_labels(theme)
    except Exception:
        labels = {"tip_cleared": "Tip cleared", "strength_reinforced": "Strength reinforced"}
    tip_cleared = labels["tip_cleared"]
    strength_reinforced = labels["strength_reinforced"]
    lines: list[str] = []
    lines.append("<coach-tip-complete>")
    lines.append(
        "One or more tips you fired earlier have been COMPLETED since last "
        "turn (detected from tool_use events in the transcript). Render ONE "
        "small ack banner per completion at the TOP of your response, above "
        "any other content. Keep them tight — one or two lines each, not a paragraph. "
        "The banners are the dopamine; don't add commentary."
    )
    lines.append("")
    lines.append("Banner shape (pre-computed per entry — render verbatim):")
    lines.append("")
    action_labels = {
        "test_run":    ("test runner detected", 2),
        "commit":      ("git commit detected",  1),
        "skill_invoke": ("skill invoked",        SKILL_XP_PER_UNIQUE),
    }
    for tip_id, entry in entries:
        spec = entry.get("spec") or {}
        kind = entry.get("kind", "weakness")
        entry_id = entry.get("entry_id") or ""
        # Earning-surface contract (v1.0.10): weakness-tip acks fire
        # when the user just took the positive action — name the action,
        # not the bad habit. Strength acks already render the positive
        # pattern. Skill kind uses the literal slash-command form
        # downstream so the display name here is unused for that branch.
        entry_display = _display_name(
            entry_id, profile, positive_frame=(kind == "weakness")
        )
        streak = int(
            entry.get("positive_streak" if kind == "strength" else "clean_streak", 0)
        )
        action = spec.get("action")
        if env == "ide":
            # Blank line before bottom `---` is load-bearing: without it,
            # CommonMark renderers fuse the body line into a setext H2
            # heading, swallowing the closing rule.
            if action == "skill_invoke" and kind == "skill":
                banner = (
                    f"  ---\n"
                    f"  ✅ **{tip_cleared}** — `/{entry_id}` invoked · "
                    f"`+{SKILL_XP_PER_UNIQUE} XP banked this session`\n"
                    f"\n"
                    f"  ---"
                )
            elif action in action_labels:
                label, xp = action_labels[action]
                bar = _streak_bar(streak, fill_glyph="🟢")
                if kind == "strength":
                    banner = (
                        f"  ---\n"
                        f"  💪 **{strength_reinforced}** — `{entry_display}` · "
                        f"`{label}` · `+{xp} XP` · `strength streak {bar}`\n"
                        f"\n"
                        f"  ---"
                    )
                else:
                    banner = (
                        f"  ---\n"
                        f"  ✅ **{tip_cleared}** — `{entry_display}` · `{label}` · "
                        f"`+{xp} XP banked` · `streak {bar}`\n"
                        f"\n"
                        f"  ---"
                    )
            else:
                xp = int(spec.get("xp", 0) or 0)
                desc = spec.get("description") or action or "action detected"
                xp_pill = f" · `+{xp} XP`" if xp > 0 else ""
                prefix = strength_reinforced if kind == "strength" else tip_cleared
                emoji = "💪" if kind == "strength" else "✅"
                banner = (
                    f"  ---\n"
                    f"  {emoji} **{prefix}** — `{entry_display}` · `{desc}`{xp_pill}\n"
                    f"\n"
                    f"  ---"
                )
            lines.append(banner)
            continue
        # Terminal path
        if action == "skill_invoke" and kind == "skill":
            banner = (
                f"  > ✅ **{tip_cleared}** — `/{entry_id}` invoked · "
                f"`+{SKILL_XP_PER_UNIQUE} XP` banked this session"
            )
        elif action in action_labels:
            label, xp = action_labels[action]
            bar = _streak_bar(streak, fill_glyph="🟢")
            if kind == "strength":
                lines.append(f"  > 💪 {strength_reinforced} — {label}")
                lines.append(f"  > +{xp} XP · {entry_display} strength streak {bar}")
                continue
            prefix = strength_reinforced if kind == "strength" else tip_cleared
            streak_label = "strength streak" if kind == "strength" else "streak"
            banner = (
                f"  > ✅ **{prefix}** — {label} · `+{xp} XP` · "
                f"`{entry_display}` {streak_label} {bar}"
            )
        else:
            xp = int(spec.get("xp", 0) or 0)
            desc = spec.get("description") or action or "action detected"
            xp_text = f" · `+{xp} XP`" if xp > 0 else ""
            prefix = strength_reinforced if kind == "strength" else tip_cleared
            banner = f"  > ✅ **{prefix}** — {desc}{xp_text} · `{entry_display}`"
        lines.append(banner)
    lines.append("")
    lines.append("Rules:")
    if env == "ide":
        lines.append("  • Render banners VERBATIM including the `---` horizontal")
        lines.append("    rules that frame each banner — they're the visual signature")
        lines.append("    that distinguishes coach output from regular chat content")
        lines.append("    in the IDE chat panel.")
        lines.append("  • Stack adjacent banners by collapsing the bottom rule of")
        lines.append("    one into the top rule of the next (so two banners share")
        lines.append("    one `---` between them).")
    else:
        lines.append("  • Render banners VERBATIM including backticks, emojis, and the")
        lines.append("    leading `> ` blockquote marker so they render dim/gray and")
        lines.append("    don't visually compete with your main response body.")
        lines.append("  • Stack them consecutively if there are multiple, no blank")
        lines.append("    lines between. One blank line between the last banner and")
        lines.append("    the rest of your response.")
    lines.append("  • These announce once — the context won't repeat next turn.")
    lines.append("</coach-tip-complete>")
    return "\n".join(lines)


def _streak_stage_label(kind: str, streak: int, target: int) -> str:
    """User-facing progress stage for ambient tip attribution. Same
    🌡️/🌶️/🔥/🏆 ladder for both weakness and strength — the kind
    distinction lives in the tail wording (`+5 bonus` vs `+5 mastery
    bonus`), set by `_xp_attribution()`. `kind` stays in the signature
    so callers don't need to change."""
    del kind  # unified ladder; both kinds render the same stage labels
    if streak >= target:
        return "🏆 Mastered"
    if streak >= 4:
        return "🔥 Streak"
    if streak >= 3:
        return "🌶️ Heating up"
    if streak >= 2:
        return "♨️ Let 'em cook"
    if streak >= 1:
        return "🌡️ Warming up"
    return "🧊 Ice cold"


def _xp_attribution(tip: dict, env: str = "terminal") -> list[str]:
    """Build the attribution lines that show WHY this tip is worth following.
    Returns one or two lines: the per-action reward line (when a
    reward_hint is present) and the streak/graduation line. Keeping the
    streak portion on its own line prevents the long single-line wrap that
    made the bar hard to read.

    Terminal shape uses italics (`_text_`) so theme-driven dim styling
    applies. IDE shape uses inline-code spans (`` `text` ``) which render
    as pill-styled badges in IDE chat panels.
    """
    streak = int(tip.get("clean_streak", 0))
    target = GRADUATION_STREAK_TARGET
    bar = _streak_bar(streak, target, fill_glyph="🟥", empty_glyph="⬜️", full_glyph="🔥")

    kind = tip.get("kind", "weakness")
    entry_id = tip.get("entry_id") or ""

    # Per-env wrappers: italics for terminal (theme-dimmed), code-span pills
    # for IDE (renders as badge backgrounds in chat-panel WebViews).
    if env == "ide":
        wrap = lambda s: f"`{s}`"  # noqa: E731
    else:
        wrap = lambda s: f"_{s}._"  # noqa: E731

    if kind == "skill":
        return [wrap(f"↑ +{SKILL_XP_PER_UNIQUE} for trying /{entry_id}")]

    if kind == "strength":
        streak = int(tip.get("positive_streak", 0) or 0)
        bar = _streak_bar(streak, target, fill_glyph="🟥", empty_glyph="⬜️", full_glyph="🔥")
        ready = streak >= target
        grad_tail = (
            f"→ +{GRADUATION_XP} mastery bonus ready"
            if ready
            else f"→ +{GRADUATION_XP} mastery bonus at {target}/{target}"
        )
        stage = _streak_stage_label(kind, streak, target)
        streak_line = wrap(f"{stage} {bar} {streak}/{target} {grad_tail}")
        hint = tip.get("reward_hint")
        if isinstance(hint, dict):
            xp = int(hint.get("xp", 0))
            desc = hint.get("description") or hint.get("action") or ""
            if xp > 0 and desc:
                return [wrap(f"↑ +{xp} per {desc}"), streak_line]
        return [streak_line]

    # weakness
    ready = streak >= target
    grad_tail = (
        f"→ +{GRADUATION_XP} bonus ready"
        if ready
        else f"→ +{GRADUATION_XP} bonus at {target}/{target}"
    )
    stage = _streak_stage_label(kind, streak, target)
    streak_line = wrap(f"{stage} {bar} {streak}/{target} {grad_tail}")
    hint = tip.get("reward_hint")
    if isinstance(hint, dict):
        xp = int(hint.get("xp", 0))
        desc = hint.get("description") or hint.get("action") or ""
        if xp > 0 and desc:
            return [wrap(f"↑ +{xp} per {desc}"), streak_line]
    return [streak_line]


def _weight_for_tip(tip: dict) -> float:
    """Weighted selection input. Baseline = confidence × priority (same
    formula merge.py uses for cap eviction). Tier and streak-urgency
    multipliers bias toward newer patterns and under-progressed weaknesses.
    Floor at 0.01 so no tip is permanently starved."""
    confidence = float(tip.get("confidence", 0.5) or 0.5)
    # profile entries expose priority; skill hints don't, so default to 1.
    priority = int(tip.get("priority", 1) or 1)
    tier = tip.get("tier", "active")
    kind = tip.get("kind", "weakness")
    streak = int(tip.get("clean_streak", 0) or 0)

    tier_mult = TIER_MULTIPLIER.get(tier, 1.0)

    if kind == "weakness":
        if streak <= 1:
            streak_mult = STREAK_URGENCY_HIGH
        elif streak <= 3:
            streak_mult = STREAK_URGENCY_MID
        else:
            streak_mult = STREAK_URGENCY_LOW
    elif kind == "strength":
        positive_streak = int(tip.get("positive_streak", 0) or 0)
        if positive_streak <= 1:
            streak_mult = 1.1
        elif positive_streak <= 3:
            streak_mult = 0.9
        else:
            streak_mult = 0.6
        streak_mult *= STRENGTH_WEIGHT_MULTIPLIER
    else:
        # Skills don't have a graduation streak pressure.
        streak_mult = 1.0

    w = confidence * priority * tier_mult * streak_mult
    return max(w, 0.01)


def _pick_tip(pool: list[dict], state: dict, now: datetime) -> dict | None:
    if not pool:
        return None
    recent = state.get("last_fired", {}) or {}
    cutoff = now - timedelta(hours=TIP_PER_TIP_COOLDOWN_HOURS)
    eligible: list[dict] = []
    for tip in pool:
        last = _parse_iso(recent.get(tip["id"]))
        if last and last > cutoff:
            continue
        eligible.append(tip)
    if not eligible:
        return None
    weights = [_weight_for_tip(t) for t in eligible]
    weights = _apply_skill_share_floor(eligible, weights)
    # random.choices respects relative weights + picks with replacement — we
    # only need k=1 so "replacement" is moot. Falls back to uniform if all
    # weights are equal.
    return random.choices(eligible, weights=weights, k=1)[0]


def _session_strength_already_fired(state: dict, session_key: str | None) -> bool:
    if not session_key:
        return False
    fired = state.get("strength_fired_sessions") or {}
    return isinstance(fired, dict) and session_key in fired


def _mark_strength_fired(state: dict, session_key: str | None, now: datetime) -> None:
    if not session_key:
        return
    fired = state.setdefault("strength_fired_sessions", {})
    if not isinstance(fired, dict):
        fired = {}
        state["strength_fired_sessions"] = fired
    fired[session_key] = now.isoformat()
    if len(fired) > 20:
        oldest = sorted(
            fired.items(),
            key=lambda item: _parse_iso(item[1]) or datetime.min.replace(tzinfo=timezone.utc),
        )
        for key, _ in oldest[:-20]:
            fired.pop(key, None)


def _apply_skill_share_floor(eligible: list[dict], weights: list[float]) -> list[float]:
    """Scale skill-hint weights up so they collectively reach MIN_SKILL_SHARE
    of total weight. Prevents skill hints from being starved on heavy-
    weakness profiles. No-op if there are no skills, no non-skills, or
    skills are already at/above the floor."""
    skill_idx = [i for i, t in enumerate(eligible) if t.get("kind") == "skill"]
    if not skill_idx:
        return weights
    total = sum(weights)
    if total <= 0:
        return weights
    skill_total = sum(weights[i] for i in skill_idx)
    non_skill_total = total - skill_total
    if non_skill_total <= 0:
        return weights  # skills already 100% share
    current_share = skill_total / total
    if current_share >= MIN_SKILL_SHARE:
        return weights
    # Target: skill_total' / (skill_total' + non_skill_total) == MIN_SKILL_SHARE
    #   → skill_total' = non_skill_total × MIN_SKILL_SHARE / (1 − MIN_SKILL_SHARE)
    target_skill_total = non_skill_total * MIN_SKILL_SHARE / (1.0 - MIN_SKILL_SHARE)
    if skill_total <= 0:
        # All skill weights were floored to 0.01 and that rounded down;
        # distribute the target equally across skill slots.
        per_skill = target_skill_total / len(skill_idx)
        scaled = list(weights)
        for i in skill_idx:
            scaled[i] = per_skill
        return scaled
    scale = target_skill_total / skill_total
    scaled = list(weights)
    for i in skill_idx:
        scaled[i] = weights[i] * scale
    return scaled


def _maybe_schedule_tip(
    now: datetime,
    session_signal: set[str] | None = None,
    project_anchors: set[str] | frozenset[str] | None = None,
    behavior_evidence: dict | None = None,
    session_key: str | None = None,
    env: str = "terminal",
) -> str | None:
    """Return a tip-render instruction block, or None if no tip should fire."""
    try:
        with _locked_tip_state():
            state = _load_tip_state_unlocked()

            # Global cooldown
            last_global = _parse_iso(state.get("last_global_fire"))
            if last_global and (now - last_global).total_seconds() < TIP_GLOBAL_COOLDOWN_SEC:
                return None

            # Probability roll
            if random.random() >= TIP_FIRE_PROBABILITY:
                return None

            profile = _load_profile()
            pool = _build_tip_pool(
                profile,
                session_signal=session_signal,
                project_anchors=project_anchors,
                behavior_evidence=behavior_evidence,
            )
            if _session_strength_already_fired(state, session_key):
                pool = [tip for tip in pool if tip.get("kind") != "strength"]
            tip = _pick_tip(pool, state, now)
            if not tip:
                return None

            if tip["kind"] == "skill":
                label = random.choice(SKILL_LABELS)
            elif tip["kind"] == "strength":
                label = random.choice(STRENGTH_LABELS)
            else:
                label = random.choice(WEAKNESS_LABELS)

            xp_lines = _xp_attribution(tip, env=env)

            # Persist cooldowns + pending-completion spec (so the next prompt can ack
            # when the user actually does the thing).
            state.setdefault("last_fired", {})[tip["id"]] = now.isoformat()
            state["last_global_fire"] = now.isoformat()
            spec = _completion_spec(tip)
            if spec:
                state.setdefault("pending_completions", {})[tip["id"]] = {
                    "fired_at": now.isoformat(),
                    "spec": spec,
                    "kind": tip["kind"],
                    "entry_id": tip.get("entry_id"),
                    "clean_streak": int(tip.get("clean_streak", 0)),
                    "positive_streak": int(tip.get("positive_streak", 0)),
                    "acknowledged": False,
                }
            if tip.get("kind") == "strength":
                _mark_strength_fired(state, session_key, now)
            _save_tip_state_unlocked(state)
    except Exception:
        return None
    _log_tip_fired(tip, spec, now)

    lines: list[str] = []
    lines.append("<coach-tip>")
    lines.append(
        "The Coach scheduler fired an ambient tip for this turn. You MUST "
        "render it at the very END of your response using the multi-line shape "
        "below, preceded by a blank line. This is not the 'silent by default' "
        "path — when this block is present, the tip is REQUIRED."
    )
    lines.append("")
    if env == "ide":
        ide_label = _ide_label(label)
        lines.append("RENDER SHAPE (HR-framed coach banner — render the lines below")
        lines.append("verbatim including the `---` horizontal rules AND the blank")
        lines.append("line above the bottom `---`. The blank line is load-bearing:")
        lines.append("without it the renderer fuses the last line into a setext H2")
        lines.append("heading and drops the closing rule. The HR frame + 🦞 + bold +")
        lines.append("code-span pills are the coach's visual signature in the IDE")
        lines.append("chat panel; without them the tip blends into regular prose.):")
        lines.append("  ---")
        lines.append(f"  {ide_label} — <one sentence, grounded in this session's actual work>")
        for _xp_line in xp_lines:
            lines.append(f"  {_xp_line}")
        lines.append("")
        lines.append("  ---")
        lines.append("")
        lines.append("RULES:")
        lines.append("  • Render the `---` rules and the `🦞 **{label}**` header EXACTLY")
        lines.append("    as shown — they are the load-bearing visual signature.")
        lines.append("  • Body is one sentence, addressed to the user (second person).")
        lines.append("  • Ground it in something concrete from THIS session's work, not")
        lines.append("    the raw pattern text below. Generic advice = failed tip.")
        lines.append("  • No 'should'. Observe, suggest, offer.")
        lines.append("  • Don't narrate what you just did — forward-looking only.")
        if tip["kind"] == "strength":
            lines.append("  • This is reinforcement, not correction: name the useful habit")
            lines.append("    and invite the user to repeat it at the next natural checkpoint.")
        else:
            lines.append("  • Phrase the body as a SPECIFIC ACTION the user could take in")
            lines.append("    the next few minutes, not abstract advice. E.g. 'run the test")
            lines.append("    for the module you just edited before your next commit' beats")
            lines.append("    'remember to test'. Pro-dev tone — no cheerleading, no")
            lines.append("    exclamation points. Dopamine comes from specificity + the")
            lines.append("    reward line, not hype.")
        lines.append("  • The reward attribution lines are PRE-COMPUTED inline-code spans.")
        lines.append("    Render each one VERBATIM (including the surrounding backticks)")
        lines.append("    on its own line directly below the header. The backticks become")
        lines.append("    pill backgrounds in the IDE chat panel — that's the badge look.")
    else:
        lines.append("RENDER SHAPE (one tip line + the pre-computed reward attribution lines,")
        lines.append("in this exact order — each PREFIXED with a markdown blockquote `> ` so")
        lines.append("the coach text renders in the dim/gray color and visually steps back")
        lines.append("from your main response body):")
        lines.append(f"  > {label} <one sentence, grounded in this session's actual work>")
        for _xp_line in xp_lines:
            lines.append(f"  > {_xp_line}")
        lines.append("")
        lines.append("RULES:")
        lines.append("  • Use the label EXACTLY as given above. Don't swap the emoji or")
        lines.append("    wording — the scheduler picked it to rotate across the session.")
        lines.append("  • Body is one sentence, addressed to the user (second person).")
        lines.append("  • Ground it in something concrete from THIS session's work, not")
        lines.append("    the raw pattern text below. Generic advice = failed tip.")
        lines.append("  • No 'should'. Observe, suggest, offer.")
        lines.append("  • Don't narrate what you just did — forward-looking only.")
        if tip["kind"] == "strength":
            lines.append("  • This is reinforcement, not correction: name the useful habit")
            lines.append("    and invite the user to repeat it at the next natural checkpoint.")
        else:
            lines.append("  • Phrase the body as a SPECIFIC ACTION the user could take in")
            lines.append("    the next few minutes, not abstract advice. E.g. 'run the test")
            lines.append("    for the module you just edited before your next commit' beats")
            lines.append("    'remember to test'. Pro-dev tone — no cheerleading, no")
            lines.append("    exclamation points. Dopamine comes from specificity + the")
            lines.append("    reward line, not hype.")
        lines.append("  • The reward attribution lines are PRE-COMPUTED. Render each one")
        lines.append("    VERBATIM on its own line directly below the tip sentence, in the")
        lines.append("    order shown. Do NOT edit the numbers, streaks, or labels inside")
        lines.append("    them, and do NOT collapse them onto the same line — the streak")
        lines.append("    bar is intentionally on its own row so it doesn't wrap.")
        lines.append("  • EVERY line (tip sentence + each attribution line) goes inside a")
        lines.append("    markdown blockquote (`> ` prefix) so they render in the dim/gray")
        lines.append("    color and don't compete visually with the white chat-completion")
        lines.append("    prose above them.")
    lines.append("")
    lines.append(f"TIP KIND: {tip['kind']}")
    lines.append(f"PATTERN / SKILL: {tip['name']}  (tier: {tip['tier']})")
    lines.append(f"UNDERLYING NUDGE (diagnostic — do NOT quote verbatim):")
    lines.append(f"  {tip['nudge']}")
    if tip["example"]:
        lines.append(f"PRIOR EVIDENCE: {tip['example']}")
    lines.append("</coach-tip>")
    return "\n".join(lines)


def main() -> None:
    try:
        payload: dict = {}
        try:
            raw = sys.stdin.read()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
        except Exception:
            payload = {}

        # v0.1.20: intercept `/plugin uninstall coach-claw@coach-claw-plugins`
        # to remind the user about the pre-uninstall cleanup step. Claude
        # Code's /plugin uninstall is not extensible — no lifecycle hooks —
        # so the plugin's statusLine entry persists in settings.json after
        # uninstall, and the user has no in-product warning. This hook
        # fires AT uninstall attempt, blocks the prompt via exit 2, and
        # surfaces the cleanup instructions inline. Gated on
        # CLAUDE_PLUGIN_ROOT so CLI users (who don't have /plugin) never
        # see it.
        if os.environ.get("CLAUDE_PLUGIN_ROOT"):
            prompt_text = str(payload.get("prompt") or payload.get("user_prompt") or "")
            if _is_coach_plugin_uninstall(prompt_text):
                marker = COACH_DIR / ".uninstall-prepped"
                if not marker.exists():
                    sys.stderr.write(_uninstall_intercept_message())
                    sys.exit(2)

        # Real Claude Code UserPromptSubmit events always carry session_id
        # and/or transcript_path. Without one (ad-hoc smoke test or
        # malformed input), reading and consuming pending markers would
        # add a sentinel session_key to consumed_by — real sessions then
        # see the marker as already-consumed and skip rendering. Exit
        # silently to keep marker state clean.
        if not (
            payload.get("session_id")
            or payload.get("sessionId")
            or payload.get("transcript_path")
            or payload.get("transcriptPath")
        ):
            _emit(None)
            return

        if _disabled():
            _emit(None)
            return

        now = datetime.now(timezone.utc)
        # Detect render env once per invocation; thread it to every renderer
        # so the whole emitted block uses one consistent shape (terminal
        # blockquote OR IDE HR-frame, never a mix).
        env = detect_render_env()
        transcript = _find_transcript(payload)
        # Stable per-session identifier. Used by _read_and_consume() so each
        # concurrent Claude Code session sees a one-shot celebration marker
        # exactly once. transcript_path is unique per session and per machine
        # restart; session_id / sessionId are the documented fallbacks.
        session_key = (
            str(transcript)
            if transcript is not None
            else str(payload.get("session_id") or payload.get("sessionId") or "")
            or None
        )

        # Check pending completions BEFORE firing a new tip, so the ack banner
        # for the last tip lands on the same response as the next tip.
        with _locked_tip_state():
            tip_state = _load_tip_state_unlocked()
            completions = _detect_completions(tip_state, transcript)
            if completions:
                pending = tip_state.setdefault("pending_completions", {})
                for tip_id, _ in completions:
                    if tip_id in pending and isinstance(pending[tip_id], dict):
                        pending[tip_id]["acknowledged"] = True
                        pending[tip_id]["acknowledged_at"] = now.isoformat()
                        _log_tip_completed(tip_id, pending[tip_id], now)
                # Prune acknowledged entries older than 24h to keep state tidy.
                cutoff = now - timedelta(hours=24)
                for tip_id in list(pending.keys()):
                    entry = pending.get(tip_id)
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("acknowledged"):
                        ack_at = _parse_iso(entry.get("acknowledged_at"))
                        if ack_at and ack_at < cutoff:
                            pending.pop(tip_id, None)
                _save_tip_state_unlocked(tip_state)
        # Theme is read once and used by both the completion banner
        # (per-theme "Tip cleared" labels) and the celebrate dispatch
        # (bespoke streak shapes). Failures fall back to "craft" so a
        # missing/corrupt config can never break rendering.
        try:
            theme = _get_theme()
        except Exception:
            theme = "craft"
        # Profile is read once and threaded into every banner that
        # mentions a pattern by name — display_name() resolves entry_ids
        # to user-facing wording (curated override → profile.name →
        # humanized slug). Failures fall back to None so display_name
        # uses the slug-humanization path.
        try:
            display_profile = _load_profile()
        except Exception:
            display_profile = None

        completion_block: str | None = None
        if completions:
            completion_block = _completion_banner(
                completions, env=env, theme=theme, profile=display_profile
            )

        levelup = _read_and_consume(LEVELUP_MARKER, session_key, now)
        grad_data = _read_and_consume(GRADUATION_MARKER, session_key, now)
        reg_data = _read_and_consume(REGRESSION_MARKER, session_key, now)
        streak_data = _read_and_consume(STREAK_REWARD_MARKER, session_key, now)

        def _items(payload: dict | None, key: str) -> list[dict]:
            if not isinstance(payload, dict):
                return []
            raw = payload.get(key)
            if not isinstance(raw, list):
                return []
            return [x for x in raw if isinstance(x, dict)]

        grads = _items(grad_data, "graduations")
        regs = _items(reg_data, "regressions")
        streak_rewards = _items(streak_data, "rewards")

        caught_up = any(
            _marker_predates_today(p, now)
            for p in (levelup, grad_data, reg_data, streak_data)
        )

        # `theme` was loaded above (used by both _completion_banner and
        # the bespoke celebrate dispatch). Streak window is optional and
        # only consumed by the bespoke dispatch.
        streak_oldest = None
        if isinstance(streak_data, dict):
            streak_oldest = _parse_iso(streak_data.get("oldest_entry_at"))

        celebrate_block = _assemble_celebrate_block(
            grads=grads,
            regs=regs,
            streak_rewards=streak_rewards,
            levelup=levelup,
            caught_up=caught_up,
            env=env,
            theme=theme,
            now=now,
            streak_oldest=streak_oldest,
            profile=display_profile,
        )
        celebrate_blocks: list[str] = [celebrate_block] if celebrate_block else []

        session_signal, project_anchors = _session_signal(transcript, payload.get("cwd"))
        behavior_evidence = _session_behavior_evidence(transcript)
        tip_block = _maybe_schedule_tip(
            now,
            session_signal=session_signal,
            project_anchors=project_anchors,
            behavior_evidence=behavior_evidence,
            session_key=session_key,
            env=env,
        )

        parts: list[str] = []
        if completion_block:
            parts.append(completion_block)
        if celebrate_blocks:
            parts.append("\n".join(celebrate_blocks))
        if tip_block:
            parts.append(tip_block)
        cron_block = _maybe_cron_nudge_block(env)
        if cron_block:
            parts.append(cron_block)
        wrap_announce = _maybe_wrap_announce_block(session_key, now, env)
        if wrap_announce:
            parts.append(wrap_announce)
        wrap_duplicate = _maybe_wrap_duplicate_block(session_key, now, env)
        if wrap_duplicate:
            parts.append(wrap_duplicate)

        if not parts:
            _emit(None)
            return

        _emit("\n\n".join(parts))
    except Exception:
        _emit(None)


if __name__ == "__main__":
    main()
