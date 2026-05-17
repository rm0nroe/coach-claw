#!/usr/bin/env python3
"""
Coach Claw — SessionStart hook.

Reads ~/.claude/coach/profile.yaml and emits a short additionalContext block
telling Claude which behavior patterns to watch for this session. Claude
decides whether to surface a footnote in its own voice when one of the
patterns actually shows up — the hook never writes the nudge itself.

Design invariants:
  - Always exits 0. A broken coach must never block a Claude Code session.
  - Emits valid JSON or nothing. No stderr leakage into the UI.
  - Respects COACH_DISABLE=1 env and ~/.claude/coach/.disabled flag file.
  - Reads only. Writes only the "last seen session start" stamp for changelog
    deltas. No profile mutation here — that's /coach-insights' job.
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Resolve the coach state dir BEFORE adding bin/ to sys.path — the helper
# we'd otherwise import (`coach_paths.resolve_coach_dir`) lives in that
# very dir, so we have to inline the env-var contract here. Keep this in
# sync with `coach/bin/coach_paths.py:resolve_coach_dir()`.
_COACH_BASE = os.environ.get("COACH_CONFIG_DIR")
COACH_DIR = Path(_COACH_BASE) if _COACH_BASE else Path.home() / ".claude" / "coach"
PROFILE = COACH_DIR / "profile.yaml"
CHANGELOG = COACH_DIR / "changelog.md"
LAST_SEEN = COACH_DIR / ".last_session_start"
DISABLED_FLAG = COACH_DIR / ".disabled"

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
    from render_env import detect_render_env  # noqa: E402
except Exception:
    # Defensive fallback: missing helper → terminal shape (which renders fine
    # everywhere, just without IDE-specific polish).
    def detect_render_env(env=None):  # type: ignore[no-redef]
        return "terminal"

MAX_INJECTED = 5               # top N entries shown per session
PROBATIONARY_DAYS = 7          # length of probation window
PROBATIONARY_FIRE_RATE = 0.30  # probability a probationary entry is shown
COOLDOWN_HOURS = 24            # per-entry min hours between firings
MIN_CONFIDENCE = 0.30          # below this = auto-filtered


def _emit(additional_context: str | None) -> None:
    """Emit the hook JSON envelope (or nothing) and exit 0."""
    if additional_context:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional_context,
            }
        }
        sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def _should_silence() -> bool:
    if os.environ.get("COACH_DISABLE") == "1":
        return True
    if DISABLED_FLAG.exists():
        return True
    return False


def _load_profile() -> dict:
    import yaml
    with PROFILE.open("r") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {"entries": []}
    data.setdefault("entries", [])
    return data


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if len(s) == 10:  # date only
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_on_cooldown(entry: dict, now: datetime) -> bool:
    last = _parse_iso(entry.get("last_fired"))
    if last is None:
        return False
    return (now - last) < timedelta(hours=COOLDOWN_HOURS)


def _probationary_fire(entry: dict, now: datetime) -> bool:
    """Roll probability for probationary entries; active entries always pass."""
    tier = entry.get("tier", "active")
    if tier != "probationary":
        return True
    promoted_at = _parse_iso(entry.get("promoted_at"))
    first_seen = _parse_iso(entry.get("first_seen"))
    anchor = promoted_at or first_seen or now
    if (now - anchor) > timedelta(days=PROBATIONARY_DAYS):
        return True  # past probation window — treat as active
    return random.random() < PROBATIONARY_FIRE_RATE


def _select_entries(profile: dict, now: datetime) -> list[dict]:
    active_pool = []
    for e in profile.get("entries", []):
        if not isinstance(e, dict):
            continue
        tier = e.get("tier", "active")
        if tier == "candidate":
            continue  # not promoted yet
        if float(e.get("confidence", 0)) < MIN_CONFIDENCE:
            continue
        if _is_on_cooldown(e, now):
            continue
        if not _probationary_fire(e, now):
            continue
        active_pool.append(e)

    active_pool.sort(
        key=lambda e: (
            float(e.get("confidence", 0)) * int(e.get("priority", 1)),
        ),
        reverse=True,
    )
    return active_pool[:MAX_INJECTED]


def _changelog_delta() -> str | None:
    """Lines added to changelog since the last SessionStart."""
    if not CHANGELOG.exists():
        return None
    try:
        last_ts = _parse_iso(LAST_SEEN.read_text().strip()) if LAST_SEEN.exists() else None
    except Exception:
        last_ts = None

    now = datetime.now(timezone.utc)
    try:
        LAST_SEEN.write_text(now.isoformat())
    except Exception:
        pass

    if last_ts is None:
        return None  # first run — no delta to show

    mtime = datetime.fromtimestamp(CHANGELOG.stat().st_mtime, tz=timezone.utc)
    if mtime <= last_ts:
        return None

    text = CHANGELOG.read_text()
    lines = [ln for ln in text.splitlines() if ln.strip().startswith(("20", "- 20"))]
    if not lines:
        return None

    recent = []
    for ln in lines[-5:]:
        try:
            date_part = ln.lstrip("- ").split(":", 1)[0]
            ts = _parse_iso(date_part)
            if ts and ts > last_ts:
                recent.append(ln)
        except Exception:
            continue

    if not recent:
        return None
    return "\n".join(recent)


def _format_context(
    entries: list[dict],
    changelog_delta: str | None,
    skill_hints: list[dict],
    env: str = "terminal",
) -> str:
    if not entries and not changelog_delta and not skill_hints:
        return ""

    parts: list[str] = []
    parts.append("<coach>")
    parts.append(
        "This block installs an ambient coaching layer on top of your normal "
        "work. The coach exists to teach the USER — not to narrate your own "
        "behavior back at them. Every tip is addressed to the user (you/your) "
        "and teaches them something about working with Claude Code more "
        "effectively. The user sees these as 'Coach tips', not as Claude "
        "reflecting on itself."
    )
    parts.append("")
    parts.append("WHEN TO SURFACE A TIP")
    parts.append("  • Silent by default. Only speak when you have something")
    parts.append("    concretely useful to teach the user about how they collaborate")
    parts.append("    with Claude Code — given what the session is actually doing.")
    parts.append("  • Ambient, not one-shot: tips can appear in multiple responses")
    parts.append("    across the session at natural moments (end of a major step,")
    parts.append("    after a visible pattern, before starting something the user")
    parts.append("    has installed tooling for). Do NOT tip on every response —")
    parts.append("    aim for a light touch. When in doubt, stay quiet.")
    parts.append("  • Never surface the SAME underlying pattern or skill twice in")
    parts.append("    one session. Rotate what you notice.")
    parts.append("")
    parts.append("VOICE — this is the whole point, get it right")
    parts.append("  • Second person or imperative: 'you', 'your', 'try …',")
    parts.append("    'consider …', 'worth …'. Address the user.")
    parts.append("  • NEVER first person about Claude. NEVER narrate what you")
    parts.append("    (Claude) just did back at the user — they watched it happen.")
    parts.append("    A tip is forward-looking teaching, not a post-mortem.")
    parts.append("  • NEVER use the word 'should'. Observe, suggest, offer.")
    parts.append("  • Translate observed patterns (Watch list below) into advice")
    parts.append("    about the USER's workflow. Example:")
    parts.append("      GOOD: *Tip:* kicking off a quick pytest run after batches")
    parts.append("            of edits like this one catches regressions while the")
    parts.append("            context is still fresh.")
    parts.append("      BAD:  *Tip:* I edited 10 files without running tests.")
    parts.append("      BAD:  *Tip:* You should run tests after editing files.")
    parts.append("  • Vary phrasing. Different opening, different rhythm each time.")
    parts.append("    The user will see many of these; a templated shape gets dull.")
    parts.append("")
    parts.append("FORMAT")
    parts.append("  • Own line, preceded by a blank line. Place at the end of the")
    parts.append("    response, or at a natural pause between major steps.")
    if env == "ide":
        parts.append("  • Shape — HR-framed coach banner (the IDE chat panel renders")
        parts.append("    `---` as a sharp horizontal rule and inline `code spans` as")
        parts.append("    pill backgrounds; together they form the coach's visual")
        parts.append("    signature):")
        parts.append("      ---")
        parts.append("      🦞 **<Label>** — <one sentence of advice>")
        parts.append("")
        parts.append("      ---")
        parts.append("    The blank line above the bottom `---` is load-bearing:")
        parts.append("    without it the CommonMark renderer fuses the body into a")
        parts.append("    setext H2 heading and drops the closing rule.")
        parts.append("    The 🦞 + bold label + HR rules are the load-bearing visual")
        parts.append("    cue. Blockquotes (`> `) render with no visible styling in")
        parts.append("    the IDE chat panel — do NOT use them.")
        parts.append("  • Rotate labels — pick ONE per tip, vary across the session.")
        parts.append("    The label pool differs by tip flavor (see TIP FLAVORS below):")
        parts.append("      weakness:   **Tip**  **Pointer**  **Heads up**  **Worth noting**")
        parts.append("      strength:   **Nice**  **Strong move**  **Solid**  **Locked in**")
        parts.append("                  **On track**  **Well done**")
        parts.append("      skill:      **Coach**  **From Coach Claw**")
        parts.append("    Always prefix the label with the 🦞 emoji and wrap in `**bold**`.")
        parts.append("    Drop the trailing colon — the `—` em-dash separator follows.")
        parts.append("  • One sentence. Concrete, specific to the session, never")
        parts.append("    generic advice that could apply to anyone.")
    else:
        parts.append("  • Shape: `> *Label:*` (italic label inside a markdown")
        parts.append("    blockquote) + space + one sentence of plain-text advice.")
        parts.append("    The blockquote prefix is load-bearing — terminal Claude")
        parts.append("    Code themes render `> ` quoted text in dim/gray, which")
        parts.append("    visually steps the coach line back from the main")
        parts.append("    response prose. Only the label is styled inside the")
        parts.append("    blockquote; no bold, no code fences.")
        parts.append("  • Rotate labels — pick ONE per tip, vary across the session.")
        parts.append("    The label pool differs by tip flavor (see TIP FLAVORS below):")
        parts.append("      weakness:   *Tip:*  *Pointer:*  *Heads up:*  *Worth noting:*")
        parts.append("      strength:   *Nice:*  *Strong move:*  *Solid:*  *Locked in:*")
        parts.append("                  *On track:*  *Well done:*")
        parts.append("      skill:      *Coach:*  *🦞 From Coach Claw:*")
        parts.append("  • Emojis are optional and must be content-matched, not")
        parts.append("    decorative. At most ~1 in 4 tips carries an emoji, and the")
        parts.append("    emoji is chosen for what the tip is actually about. Examples")
        parts.append("    of the kind of fit to aim for (not a menu — pick whatever")
        parts.append("    fits the specific tip):")
        parts.append("      ✏️  testing / verification advice")
        parts.append("      ⚡  performance, speed, latency")
        parts.append("      🔒  security, secrets, auth")
        parts.append("      🎯  precision, focus, scoping")
        parts.append("      🪶  simplification, reducing scope")
        parts.append("      🧭  navigation, planning, direction")
        parts.append("      📌  durability — worth pinning for later")
        parts.append("      🧠  strategy, mental model")
        parts.append("      🌿  git / branches / worktrees")
        parts.append("      💡  genuine insight / non-obvious idea")
        parts.append("    Place the emoji before the label: `> *✏️ Tip:*`. NEVER default")
        parts.append("    to 💡 out of habit — only use it when the tip genuinely is a")
        parts.append("    fresh insight the user hadn't connected. If no emoji fits")
        parts.append("    naturally, omit it — a plain `> *Tip:*` is always fine.")
        parts.append("  • One sentence. Concrete, specific to the session, never")
        parts.append("    generic advice that could apply to anyone.")
    parts.append("")
    parts.append("THREE FLAVORS OF TIP")
    parts.append("  WEAKNESS — triggered when the session matches a pattern on")
    parts.append("    the Weaknesses list (direction=negative). Teach the user")
    parts.append("    the better habit, forward-looking.")
    parts.append("    Labels: *Tip:*, *Pointer:*, *Heads up:*, *Worth noting:*.")
    parts.append("  STRENGTH — triggered when the session shows a pattern from")
    parts.append("    the Strengths list (direction=positive). Acknowledge the")
    parts.append("    habit warmly — they're doing the thing. Not a correction.")
    parts.append("    Labels: *Nice:*, *Strong move:*, *Solid:*, *Locked in:*,")
    parts.append("    *On track:*, *Well done:*. Rotate across the session.")
    parts.append("      GOOD: *Strong move:* kicking off pytest right after that")
    parts.append("            edit batch is exactly the habit your profile tracks.")
    parts.append("      BAD:  *Nice:* good job writing code today.  ← generic")
    parts.append("    Fire sparingly — at most one strength tip per session, and")
    parts.append("    only when the strength is genuinely visible in the current")
    parts.append("    work (not just because it's on the list).")
    parts.append("  SKILL — a personalized recommendation from the user's")
    parts.append("    /coach-insights-trained profile. Must read as personalized, not")
    parts.append("    generic. Labels: *Coach:*, *🦞 From Coach Claw:*.")
    parts.append("      GOOD: *Coach:* /<skill-id> handles this exact loop end-to-end —")
    parts.append("            you've been doing the steps by hand for the last few turns.")
    parts.append("      BAD:  *Coach:* You could use /<skill-id>.")
    parts.append("    Only suggest a skill the user has NOT already invoked this")
    parts.append("    session.")

    weaknesses = [e for e in entries if e.get("direction", "negative") == "negative"]
    strengths = [e for e in entries if e.get("direction") == "positive"]

    def _render_entry(e: dict) -> None:
        name = e.get("name") or e.get("id", "unknown")
        nudge = (e.get("nudge") or "").strip()
        tier = e.get("tier", "active")
        tier_tag = " (probationary)" if tier == "probationary" else ""
        parts.append(f"  • {name}{tier_tag}: {nudge}")
        examples = e.get("examples") or []
        if isinstance(examples, list) and examples:
            ex = str(examples[0]).strip()[:120]
            if ex:
                parts.append(f"      prior evidence: \"{ex}\"")

    if weaknesses:
        parts.append("")
        parts.append("Weaknesses to watch (fire WEAKNESS-flavor tips when these show up):")
        for e in weaknesses:
            _render_entry(e)

    if strengths:
        parts.append("")
        parts.append("Strengths to reinforce (fire STRENGTH-flavor tips when these show up):")
        for e in strengths:
            _render_entry(e)

    if weaknesses or strengths:
        parts.append("")
        parts.append("  (Watch list entries are diagnostic context for YOU — do not")
        parts.append("  quote them verbatim. Translate into user-facing advice or")
        parts.append("  acknowledgment as appropriate for the flavor.)")

    if skill_hints:
        parts.append("")
        parts.append("Skill hints (installed + personally relevant — surface as SKILL tips):")
        for h in skill_hints:
            sid = h.get("id", "?")
            desc = (h.get("description") or "").strip()
            parts.append(f"  • /{sid}: {desc}")

    if changelog_delta:
        parts.append("")
        parts.append(
            "(Coach profile updated since last session — silently noted, do not "
            "mention unless asked:)"
        )
        for ln in changelog_delta.splitlines():
            parts.append(f"  {ln}")

    parts.append("</coach>")
    return "\n".join(parts)


def _maybe_install_plugin_statusline() -> None:
    """Plugin distribution only: idempotently set Coach's statusLine in
    ~/.claude/settings.json. The plugin model can't declare a top-level
    statusLine in plugin settings.json (Claude Code only honors `agent`
    and `subagentStatusLine` there), so we self-patch from inside a hook
    we already run on every session start.

    Gated on CLAUDE_PLUGIN_ROOT — only set by Claude Code when running
    under a plugin install. CLI distribution never has it set, so this
    is a no-op for CLI users (whose statusLine is managed by
    install.sh)."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not plugin_root:
        return
    try:
        from statusline_self_patch import ensure_statusline_installed
        ensure_statusline_installed(plugin_root)
    except Exception:
        # Hook failsafe is non-negotiable. Silent on any error.
        pass


def _bank_completed_sessions() -> None:
    """Fire the XP banker in the background (fire-and-forget).

    bank.py scans recently-completed transcripts and converts session XP
    to lifetime XP at 10:1. Fully silent, never blocks session start.
    """
    try:
        import subprocess
        import sys as _sys
        bank_script = COACH_DIR / "bin" / "bank.py"
        if bank_script.exists():
            # Use sys.executable so Homebrew / pyenv / system installs all
            # work without a hardcoded /usr/bin/python3.
            subprocess.Popen(
                [_sys.executable, str(bank_script)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception:
        pass


WEEKLY_INSIGHTS_MARKER = COACH_DIR / ".last_weekly_insights"
WEEKLY_INSIGHTS_THROTTLE_SECONDS = 7 * 24 * 3600


def _maybe_spawn_weekly_insights(now: datetime) -> None:
    """Spawn `insights-llm.sh` detached when the weekly throttle is stale.

    The wrapper itself enforces the 7-day throttle (see insights-llm.sh's
    --force semantics) — this hook just avoids forking when we already
    know the throttle would skip. Mirrors the bank.py spawn pattern.

    Trigger choice: SessionStart (not launchd) is deliberate. macOS
    laptops sleeping on battery silently skip launchd jobs without
    `WakeFromSleep`; firing on the user's first session of the week is
    more reliable for a coaching tool with no SLA on weekly freshness.
    """
    try:
        import subprocess
        script = COACH_DIR / "bin" / "insights-llm.sh"
        if not script.exists():
            return
        if WEEKLY_INSIGHTS_MARKER.exists():
            try:
                age = now.timestamp() - WEEKLY_INSIGHTS_MARKER.stat().st_mtime
            except OSError:
                age = WEEKLY_INSIGHTS_THROTTLE_SECONDS + 1
            if age < WEEKLY_INSIGHTS_THROTTLE_SECONDS:
                return
        subprocess.Popen(
            ["bash", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def main() -> None:
    try:
        # Read stdin (Claude Code sends session JSON). Closing prematurely
        # can cause SIGPIPE on the sender, so we always read fully even
        # when we don't use the payload.
        payload: dict = {}
        try:
            raw = sys.stdin.read()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
        except Exception:
            payload = {}

        # Real Claude Code SessionStart events always carry session_id and/or
        # transcript_path. Without one, the caller is an ad-hoc smoke test
        # or malformed input — in which case spawning bank.py (would race
        # against unknown banked_sessions writes) or weekly insights (would
        # fire a paid /insights call on a stale throttle) is wrong. Exit
        # silently with no side effects.
        if not (
            payload.get("session_id")
            or payload.get("sessionId")
            or payload.get("transcript_path")
            or payload.get("transcriptPath")
        ):
            _emit(None)
            return

        if _should_silence():
            _emit(None)
            return

        # Plugin distribution: idempotently ensure the statusLine entry
        # is in settings.json. No-op for CLI distribution (gated on
        # CLAUDE_PLUGIN_ROOT). Cheap when matched (single read).
        _maybe_install_plugin_statusline()

        # Plugin distribution: prune cache dirs older than the active
        # version. Claude Code's /plugin update never garbage-collects
        # prior versions, so they accumulate. Failsafe + gated on
        # CLAUDE_PLUGIN_ROOT. Cheap (microsecond scan, no-op when
        # cache has one dir).
        if os.environ.get("CLAUDE_PLUGIN_ROOT"):
            try:
                import cache_prune
                cache_prune.prune_inactive_cache_versions()
            except Exception:
                pass

        # Bank yesterday's session XP into lifetime XP (async, never blocks).
        _bank_completed_sessions()

        now = datetime.now(timezone.utc)

        # Trigger the weekly LLM-driven insights pass if its 7-day throttle
        # is stale. Detached, never blocks. The wrapper itself reapplies
        # the throttle check before it does any real work.
        _maybe_spawn_weekly_insights(now)

        if not PROFILE.exists():
            _emit(None)
            return

        profile = _load_profile()
        entries = _select_entries(profile, now)
        delta = _changelog_delta()
        raw_hints = profile.get("skill_hints") or []
        skill_hints = [h for h in raw_hints if isinstance(h, dict) and h.get("id")]
        env = detect_render_env()
        ctx = _format_context(entries, delta, skill_hints, env=env)
        _emit(ctx if ctx else None)
    except Exception:
        # Failsafe: coach must never block a session.
        _emit(None)


if __name__ == "__main__":
    main()
