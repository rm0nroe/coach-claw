"""Per-theme bespoke <coach-celebrate> banner shapes.

Five of the twelve themes get bespoke streak-reward + level-up rendering:
forge, ocean, skyrim, military, hacker. The other seven keep the default
shape rendered by `hooks/coach-user-prompt.py:_assemble_celebrate_block`.

Bespoke shapes are TERMINAL-ONLY. IDE rendering stays on the existing
HR-framed default for all themes — bespoke ASCII frames clash with the
WebView's proportional-ish typography.

Public API:
  BESPOKE_THEMES — frozenset of theme names with bespoke shapes
  render_celebrate_for_theme(...) — full block renderer, or None if
                                    the theme is not bespoke

Verbatim-render contract: every banner string is fully-resolved Python
text. No template-fill via the model. Pinned by literal-substring tests
in coach/tests/test_banner_themes.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from render_env import supports_dual_blade


BESPOKE_THEMES = frozenset({"forge", "ocean", "skyrim", "military", "hacker"})


# -----------------------------------------------------------------------------
# Shared helpers — the small kernel that every theme uses.

def _meter(streak: int, target: int, filled: str, empty: str) -> str:
    """Streak meter: `filled * streak + empty * (target - streak)`. Both
    glyphs MUST be 1-cell-wide on the rendering terminal — themes that use
    dual-cell-risk glyphs (e.g., skyrim's ⚔) negotiate fallback before
    calling this."""
    streak = max(0, min(streak, target))
    return filled * streak + empty * max(target - streak, 0)


def _arrow_xp(direction: str, xp: int, *, with_unit: bool = False) -> str:
    """`↑N` for positive, `↓N` for negative. Set `with_unit=True` for
    `↑N XP` (military variant)."""
    arrow = "↑" if direction == "positive" else "↓"
    return f"{arrow}{xp} XP" if with_unit else f"{arrow}{xp}"


def _format_window_phrase(now: datetime, oldest: datetime | None) -> dict:
    """Return per-theme date phrasings for the header window.

    Keys:
      relative      — "yesterday" / "2026-05-06" (skyrim/ocean)
      iso_date      — "2026-05-06" (forge / skyrim full)
      iso_datetime  — "2026-05-06 19:00" (hacker)
      now_iso_date  — "2026-05-07" (military)
      now_zulu_time — "0500Z" (military)
    """
    now_date = now.strftime("%Y-%m-%d")
    now_zulu = now.strftime("%H%MZ")
    if oldest is None:
        return {
            "relative":      "earlier",
            "iso_date":      now_date,
            "iso_datetime":  now.strftime("%Y-%m-%d %H:%M"),
            "now_iso_date":  now_date,
            "now_zulu_time": now_zulu,
        }
    iso_date = oldest.strftime("%Y-%m-%d")
    delta_days = (now.date() - oldest.date()).days
    if delta_days == 0:
        relative = "earlier today"
    elif delta_days == 1:
        relative = "yesterday"
    else:
        relative = iso_date
    return {
        "relative":      relative,
        "iso_date":      iso_date,
        "iso_datetime":  oldest.strftime("%Y-%m-%d %H:%M"),
        "now_iso_date":  now_date,
        "now_zulu_time": now_zulu,
    }


# -----------------------------------------------------------------------------
# Verb-style themes — forge, ocean, skyrim share the row skeleton:
#   `>   {meter}  {name:<W}  {verb:<V}   {arrow}{xp}`
# Differences live in the spec dict (header glyph, label, meter glyphs,
# verb words, footer template).

# Top-N cap for streak rows. Banners that ticked many patterns at once
# (e.g., catch-up after a multi-day idle) get noisy fast — show the
# closest-to-graduation N and a tail line counting the rest.
TOP_N = 5


def _sort_and_truncate(rewards: list[dict]) -> tuple[list[dict], int]:
    """Group by direction (positive first, then negative), sort each group
    by streak descending (closer-to-graduation first), then truncate to
    TOP_N. Returns (rows_to_render, hidden_count)."""
    def _key(r: dict) -> tuple:
        return (-int(r.get("streak", 0)), r.get("name") or r.get("id", "?"))

    pos = sorted(
        (r for r in rewards if r.get("direction") == "positive"),
        key=_key,
    )
    neg = sorted(
        (r for r in rewards if r.get("direction") != "positive"),
        key=_key,
    )
    ordered = pos + neg
    return ordered[:TOP_N], max(0, len(ordered) - TOP_N)


def _render_verb_style_rows(
    rewards: list[dict],
    *,
    meter_filled: str,
    meter_empty: str,
    verb_positive: str,
    verb_negative: str,
) -> list[str]:
    """Return one rendered row string per reward, in input order.

    Column rule: name padded to (max_name + 4); verb padded to max_verb
    in the input set; 3 spaces between verb and arrow. Matches the locked
    visual cadence from the user's mockups."""
    if not rewards:
        return []

    names = [r.get("name", r.get("id", "?")) for r in rewards]
    verbs = [
        verb_positive if r.get("direction") == "positive" else verb_negative
        for r in rewards
    ]
    name_pad = max(len(n) for n in names) + 4
    verb_pad = max(len(v) for v in verbs)

    out: list[str] = []
    for r, name, verb in zip(rewards, names, verbs):
        meter = _meter(
            int(r.get("streak", 0)),
            int(r.get("target", 5)),
            meter_filled,
            meter_empty,
        )
        xp = _arrow_xp(r.get("direction", "negative"), int(r.get("xp_awarded", 1)))
        out.append(
            f">   {meter}  {name:<{name_pad}}{verb:<{verb_pad}}   {xp}"
        )
    return out


# Spec dict shape — read by `_render_verb_style`.
#   header_glyph: 1-cell glyph at the front of the header line
#   header_label: themed phrase for the header (e.g., "The Anvil")
#   header_window_template: format string with {relative}/{iso_date} keys
#   meter_filled / meter_empty: 1-cell glyphs for the streak meter
#   verb_positive / verb_negative: words slotted into each row
#   levelup_glyph: 1-cell or emoji glyph at the front of the footer
#   levelup_template: format string with {name}/{level}/{next_xp} keys
SPECS: dict[str, dict] = {
    "ocean": {
        "header_glyph": "🦞",
        "header_label": "Tide turned",
        "header_window_template": "since {relative}",
        "meter_filled": "≋",
        "meter_empty":  "·",
        "verb_positive": "rising tide",
        "verb_negative": "ebbing",
        "levelup_glyph": "⚓",
        # ⚓ is a 2-cell emoji; tighten the gap so it visually matches the
        # 1-cell glyph + 2-space pad used by skyrim's ⚜.
        "levelup_glyph_pad": " ",
        # 🌊Deep Water🌊 — Reefer (L8) · next fathom at 125 XP
        "levelup_template": (
            "🌊Deep Water🌊 — {name} (L{level}) · next fathom at {next_xp} XP"
        ),
        "levelup_max_template": (
            "🌊Deep Water🌊 — {name} (L{level}) · all fathoms reached"
        ),
    },
    "forge": {
        "header_glyph": "⚒",
        "header_label": "The Anvil",
        "header_window_template": "{iso_date} → now",
        "meter_filled": "▰",
        "meter_empty":  "▱",
        "verb_positive": "tempering",
        "verb_negative": "quenching",
        "levelup_glyph": "✨",
        # ✨ **Mastersmith** (L8) forged anew · next heat at 125 XP
        "levelup_template": (
            "**{name}** (L{level}) forged anew · next heat at {next_xp} XP"
        ),
        "levelup_max_template": (
            "**{name}** (L{level}) — the forge is mastered"
        ),
    },
    "skyrim": {
        # Header + meter use ⚔ (U+2694 CROSSED SWORDS). When the active
        # terminal can't render that as a single cell (TERM=dumb, non-UTF8
        # locale, kill switch), the dispatcher swaps both to ✕.
        "header_glyph": "⚔",
        "header_glyph_fallback": "✕",
        "header_label": "Saga",
        "header_window_template": "since {iso_date}",
        "meter_filled": "⚔",
        "meter_filled_fallback": "✕",
        "meter_empty":  "·",
        "verb_positive": "oath kept",
        "verb_negative": "curse fades",
        "levelup_glyph": "⚜",
        # ⚜ **Pupil** (L8) — next title at 125 XP
        "levelup_template": (
            "**{name}** (L{level}) — next title at {next_xp} XP"
        ),
        "levelup_max_template": (
            "**{name}** (L{level}) — saga complete"
        ),
    },
}


# -----------------------------------------------------------------------------
# Per-theme labels for the tip-complete ack banners (B9). Every theme gets
# its own pair — `tip_cleared` (weakness completion ack) and
# `strength_reinforced` (strength completion ack). Default theme `craft`
# keeps the original wording so existing tests and behavior don't shift.
# Picked to match each theme's voice; 2-word phrases for visual parity with
# the originals; no emoji prefix so the leading ✅/💪 stays the visual
# signature.
COMPLETION_LABELS: dict[str, dict[str, str]] = {
    "craft":        {"tip_cleared": "Tip cleared",         "strength_reinforced": "Strength reinforced"},
    "forge":        {"tip_cleared": "Iron struck",         "strength_reinforced": "Edge sharpened"},
    "cosmic":       {"tip_cleared": "Course corrected",    "strength_reinforced": "Constellation drawn"},
    "ocean":        {"tip_cleared": "Wave caught",         "strength_reinforced": "Tide carries"},
    "skyrim":       {"tip_cleared": "Quest cleared",       "strength_reinforced": "Skill mastered"},
    "marvel":       {"tip_cleared": "Threat neutralized",  "strength_reinforced": "Power harnessed"},
    "dc":           {"tip_cleared": "Watch kept",          "strength_reinforced": "Beacon answered"},
    "finalfantasy": {"tip_cleared": "Encounter cleared",   "strength_reinforced": "Stat boosted"},
    "military":     {"tip_cleared": "Mission accomplished","strength_reinforced": "Drill burned in"},
    "lotr":         {"tip_cleared": "Burden lightened",    "strength_reinforced": "Heart steadfast"},
    "starwars":     {"tip_cleared": "Order kept",          "strength_reinforced": "Force grows"},
    "hacker":       {"tip_cleared": "Exploit landed",      "strength_reinforced": "Pattern indexed"},
}


def completion_labels(theme: str) -> dict[str, str]:
    """Return per-theme labels for the tip-complete ack banners. Falls
    back to the `craft` defaults for unknown themes so future theme
    additions don't crash banner rendering."""
    return COMPLETION_LABELS.get(theme) or COMPLETION_LABELS["craft"]


def _resolve_spec(theme: str, dual_blade_supported: bool) -> dict:
    """Apply glyph fallbacks to the spec for the active theme + terminal.
    Returns a plain dict — callers must not mutate the SPECS source."""
    spec = dict(SPECS[theme])
    if not dual_blade_supported:
        for key in ("header_glyph", "meter_filled"):
            fallback_key = f"{key}_fallback"
            if fallback_key in spec:
                spec[key] = spec[fallback_key]
    return spec


def _render_verb_style(
    spec: dict,
    *,
    streak_rewards: list[dict],
    levelup: dict | None,
    now: datetime,
    streak_oldest: datetime | None,
) -> str:
    """Compose the bespoke streak + levelup section for a verb-style theme.

    Returns a string (no enclosing <coach-celebrate> tags — caller wraps).
    Empty streak_rewards + no levelup yields an empty string."""
    parts: list[str] = []

    if streak_rewards:
        window = _format_window_phrase(now, streak_oldest)
        window_text = spec["header_window_template"].format(**window)
        parts.append(
            f"> {spec['header_glyph']}  {spec['header_label']} · {window_text}"
        )
        parts.append(">")
        rows_to_show, hidden = _sort_and_truncate(streak_rewards)
        parts.extend(_render_verb_style_rows(
            rows_to_show,
            meter_filled=spec["meter_filled"],
            meter_empty=spec["meter_empty"],
            verb_positive=spec["verb_positive"],
            verb_negative=spec["verb_negative"],
        ))
        if hidden > 0:
            parts.append(f">   …{hidden} more")

    if levelup:
        # Compose the level-up footer. Pull idx + level name from the
        # marker payload directly — this avoids reading bank state on the
        # hot path (compute_for_render is reserved for the military theme,
        # which actually needs ELO + medal_count).
        level = int(levelup.get("to_idx", 0)) + 1
        name = str(levelup.get("to", "?"))
        # next_xp: the threshold for the level AFTER the one we just
        # crossed. At L50 there is no next level — the renderer swaps to
        # `levelup_max_template` which omits the "next at X XP" suffix.
        next_xp = _next_xp_after_levelup(levelup)
        if next_xp == 0:
            template = spec["levelup_max_template"]
        else:
            template = spec["levelup_template"]
        line = template.format(name=name, level=level, next_xp=next_xp)
        if streak_rewards:
            parts.append(">")
        pad = spec.get("levelup_glyph_pad", "  ")
        parts.append(f"> {spec['levelup_glyph']}{pad}{line}")

    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Military theme — divergent shape (tag-prefixed rows, rank ribbon footer
# composed from stats.compute_for_render). Doesn't fit the verb-style
# helper because rows have a prefix tag instead of a verb suffix.

def _render_military(
    *,
    streak_rewards: list[dict],
    levelup: dict | None,
    now: datetime,
    streak_oldest: datetime | None,
) -> str:
    """Compose the bespoke military streak + levelup section.

    Header:
        > ◢  SITREP · 2026-05-07 · 0500Z

    Rows have a [PUSH]/[HOLD] tag prefix and use ▮▯ meter, no verb column,
    `↑N XP` / `↓N XP` (with unit):
        >   [PUSH] ▮▮▮▮▯  safe git hygiene         ↑2 XP

    Footer is a rank ribbon — medal count + Roman numeral + ELO + theme-
    aware level name + next-promotion threshold:
        >  ◆ 🎖️🎖️  Ⅷ  1263  **Sensei**  ·  promotion at 125 XP
    """
    parts: list[str] = []

    if streak_rewards:
        window = _format_window_phrase(now, streak_oldest)
        parts.append(
            f"> ◢  SITREP · {window['now_iso_date']} · {window['now_zulu_time']}"
        )
        parts.append(">")

        rows_to_show, hidden = _sort_and_truncate(streak_rewards)
        # Pad name column based on the longest visible name.
        names = [r.get("name") or r.get("id", "?") for r in rows_to_show]
        name_pad = (max((len(n) for n in names), default=0)) + 4

        for r, name in zip(rows_to_show, names):
            tag = "[PUSH]" if r.get("direction") == "positive" else "[HOLD]"
            meter = _meter(
                int(r.get("streak", 0)),
                int(r.get("target", 5)),
                "▮",
                "▯",
            )
            xp = _arrow_xp(
                r.get("direction", "negative"),
                int(r.get("xp_awarded", 1)),
                with_unit=True,
            )
            parts.append(f">   {tag} {meter}  {name:<{name_pad}}{xp}")

        if hidden > 0:
            parts.append(f">   …{hidden} more")

    if levelup:
        # Compose rank ribbon from compute_for_render. The lifetime XP at
        # the moment of levelup is `xp_at_levelup` from the marker — the
        # threshold the user just crossed. Session XP = 0 here because the
        # ribbon represents the levelup state, not in-progress slide.
        import stats  # late import — stats reads user_config at module init
        meta = stats.compute_for_render(
            int(levelup.get("xp_at_levelup", 0)),
            0,
        )
        # Trust the levelup payload's "to" name in case a custom theme
        # ladder differs from the live LEVELS (e.g., user changed theme
        # between marker write and read). Fall back to compute_for_render's
        # lookup if "to" is missing.
        name = str(levelup.get("to") or meta["name"])
        medals = "🎖️" * meta["medal_count"]
        if streak_rewards:
            parts.append(">")
        # At L50, compute_for_render returns next_xp=None — there is no
        # promotion threshold to render. Swap to a max-rank suffix.
        if meta["next_xp"] is None:
            tail = "·  highest grade"
        else:
            tail = f"·  promotion at {meta['next_xp']} XP"
        parts.append(
            f">  ◆ {medals}  {meta['roman']}  {meta['elo']}  "
            f"**{name}**  {tail}"
        )

    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Hacker theme — divergent shape (no verb column, snake_case names, log
# frame). Doesn't fit the verb-style helper, has its own renderer.

def _name_to_snake(name: str) -> str:
    """'safe git hygiene' → 'safe_git_hygiene'. Hacker theme convention."""
    return name.lower().replace(" ", "_").replace("-", "_")


def _render_hacker(
    *,
    streak_rewards: list[dict],
    levelup: dict | None,
    now: datetime,
    streak_oldest: datetime | None,
) -> str:
    """Compose the bespoke hacker streak + levelup section.

    Header is a 2-line shell-prompt + dashed-timestamp frame:
        > 👾 [coach@claw ~]$ tail -f session.log
        > ── 2026-05-06 19:00 → now ────────────────

    Rows use a shell-coded RUN/KILL prefix so direction is preserved
    (both directions earn XP, but they're semantically different events):
        >   ▓▓▓▓░  RUN  safe_git_hygiene             [↑2 xp]
        >   ▓▓▓▓░  KILL heavy_subagent_delegation    [↑2 xp]

    The `↑` in `[↑N xp]` denotes direction of XP movement (always up,
    since both row types are gains). RUN/KILL encodes which kind of
    pattern produced the gain — strength reinforced vs weakness retired.

    Tail (when truncated): ASCII `...` and a help hint:
        >   ...4 more  (cat /coach/status)

    Footer: 2-line uplink/breach shape:
        > ::  📡 UPLINK ↑  L8 / Sensei 🥷 ::
        > next breach 🔓 125 xp
    """
    parts: list[str] = []

    if streak_rewards:
        window = _format_window_phrase(now, streak_oldest)
        parts.append("> 👾 [coach@claw ~]$ tail -f session.log")
        # The trailing dashes pad the timestamp line to a fixed visual
        # width — matches the locked mockup's `── ... ────────────────`
        # shape. Width chosen so that yesterday-19:00 phrase sits in the
        # middle of the line. 16 trailing dashes covers the locked length.
        parts.append(f"> ── {window['iso_datetime']} → now ────────────────")
        parts.append(">")

        rows_to_show, hidden = _sort_and_truncate(streak_rewards)
        snake_names = [_name_to_snake(r.get("name") or r.get("id", "?"))
                       for r in rows_to_show]
        name_pad = (max((len(n) for n in snake_names), default=0)) + 4

        for r, sname in zip(rows_to_show, snake_names):
            meter = _meter(
                int(r.get("streak", 0)),
                int(r.get("target", 5)),
                "▓",
                "░",
            )
            xp = int(r.get("xp_awarded", 1))
            # RUN / KILL — direction prefix in shell-coded vocabulary.
            # Both 4 chars wide so the snake_case name column stays
            # aligned across mixed-direction banners.
            prefix = "RUN " if r.get("direction") == "positive" else "KILL"
            parts.append(
                f">   {meter}  {prefix} {sname:<{name_pad}}[↑{xp} xp]"
            )

        if hidden > 0:
            parts.append(f">   ...{hidden} more  (cat /coach/status)")

    if levelup:
        level = int(levelup.get("to_idx", 0)) + 1
        name = str(levelup.get("to", "?"))
        next_xp = _next_xp_after_levelup(levelup)
        if streak_rewards:
            parts.append(">")
        parts.append(f"> ::  📡 UPLINK ↑  L{level} / {name} 🥷 ::")
        # At L50, no next breach — swap the threshold line for a max-rank
        # marker that doesn't promise more progression.
        if next_xp == 0:
            parts.append("> root access 🔓 max layer reached")
        else:
            parts.append(f"> next breach 🔓 {next_xp} xp")

    return "\n".join(parts)


def _next_xp_after_levelup(levelup: dict) -> int:
    """The XP threshold for the level immediately after the one the user
    just crossed. Read from the active LEVELS ladder in stats — themed
    naming + ELO range honor the user's /config selection automatically.

    Returns 0 when at L50 (no next level)."""
    import stats  # late import: stats reads user_config at module init
    to_idx = int(levelup.get("to_idx", 0))
    next_idx = to_idx + 1
    if next_idx >= len(stats.LEVELS):
        return 0
    return int(stats.LEVELS[next_idx][0])


# -----------------------------------------------------------------------------
# Public dispatch.

def render_celebrate_for_theme(
    theme: str,
    *,
    streak_rewards: list[dict],
    levelup: dict | None,
    grads_block: str = "",
    regs_block: str = "",
    now: datetime,
    streak_oldest: datetime | None = None,
    dual_blade_supported: bool | None = None,
    caught_up: bool = False,
) -> str | None:
    """Return the full <coach-celebrate>...</coach-celebrate> block for a
    bespoke theme, or None if the theme isn't in BESPOKE_THEMES (caller
    falls back to default rendering).

    Composition order inside the block:
      1. Verbatim-render preamble (same as default).
      2. Pre-rendered regressions block (default-shape, may be empty).
      3. Bespoke streak section (theme header + rows).
      4. Pre-rendered graduations block (default-shape, may be empty).
      5. Bespoke level-up footer.

    Steps 2 + 4 are passed in as already-rendered strings — the hook calls
    `_regression_block` / `_graduation_block` from coach-user-prompt.py to
    produce them (avoids a circular import).

    `dual_blade_supported`: explicit override for tests. None means
    "probe live env" — production callers leave this unset.

    Returns None if the bespoke render produces no body (no streak rewards,
    no levelup, no grads, no regs) — caller should not emit an empty block.
    """
    if theme not in BESPOKE_THEMES:
        return None

    if dual_blade_supported is None:
        dual_blade_supported = supports_dual_blade()

    if theme in SPECS:
        spec = _resolve_spec(theme, dual_blade_supported)
        bespoke_section = _render_verb_style(
            spec,
            streak_rewards=streak_rewards,
            levelup=levelup,
            now=now,
            streak_oldest=streak_oldest,
        )
    elif theme == "hacker":
        bespoke_section = _render_hacker(
            streak_rewards=streak_rewards,
            levelup=levelup,
            now=now,
            streak_oldest=streak_oldest,
        )
    elif theme == "military":
        bespoke_section = _render_military(
            streak_rewards=streak_rewards,
            levelup=levelup,
            now=now,
            streak_oldest=streak_oldest,
        )
    else:  # pragma: no cover — guard against future theme key drift
        return None

    # Guard: if every section is empty, return None so the caller doesn't
    # emit a vacuous <coach-celebrate>...</coach-celebrate>.
    if not (bespoke_section or grads_block or regs_block):
        return None

    out: list[str] = ["<coach-celebrate>"]
    out.append(
        "The block below is a pre-rendered set of milestone banners. "
        "Render this block VERBATIM at the very top of your next response, "
        "BEFORE any other content, then continue with the user's request. "
        "Do NOT re-interpret labels, swap directions, change emoji, or "
        "substitute slugs for names — every character is intentional and "
        "pinned by tests."
    )
    # Catch-up framing: emit ONLY when there's no streak header to carry
    # the date phrasing. The streak header already says "since {date}",
    # making the framing line redundant for streak banners (locked v1
    # decision). Levelup-only / grad-only / reg-only bespoke banners
    # have no theme header, so the framing line earns its place.
    if caught_up and not streak_rewards:
        out.append("")
        out.append(
            "Milestones earned across earlier sessions — not from the "
            "command you just typed."
        )
    out.append("")

    # Order: regressions first (big news), then bespoke streak section,
    # then graduations (ceremonies between streak ticks and the level-up
    # crown), then bespoke level-up.
    if regs_block:
        out.append(regs_block)
        out.append("")
    if bespoke_section and streak_rewards:
        # Split bespoke_section into streak header+rows vs levelup tail
        # so graduations can land BETWEEN them. The marker for the split
        # is the level-up glyph line — easier to render the parts
        # separately than try to slice a composed string.
        streak_part, _, levelup_part = _split_bespoke_sections(
            bespoke_section
        )
        out.append(streak_part)
        if grads_block:
            out.append("")
            out.append(grads_block)
        if levelup_part:
            out.append("")
            out.append(levelup_part)
    elif bespoke_section:
        # Levelup-only (no streak rewards). Graduations land above.
        if grads_block:
            out.append(grads_block)
            out.append("")
        out.append(bespoke_section)
    elif grads_block:
        out.append(grads_block)

    out.append("</coach-celebrate>")
    return "\n".join(out)


def _split_bespoke_sections(body: str) -> tuple[str, str, str]:
    """Split a verb-style render into (streak_part, separator, levelup_part).

    The split is a blank `>` line that precedes the levelup glyph line.
    Returns ("body", "", "") when no levelup is present (single-section)."""
    lines = body.split("\n")
    # Find the last blank-blockquote separator. Levelup is always last.
    # If the streak section's empty divider (between header and rows) is
    # the only `>` line, there's no levelup section.
    # Heuristic: levelup is the FINAL ">" + glyph + body chunk; everything
    # before the immediately-preceding ">" line is the streak section.
    last_blank = -1
    for i, line in enumerate(lines):
        if line == ">":
            last_blank = i
    # If the last blank is followed by a blockquote line that isn't a
    # row indent (rows start with ">   ", levelup starts with "> {glyph}"),
    # treat as split point.
    if last_blank == -1 or last_blank == len(lines) - 1:
        return body, "", ""
    tail = lines[last_blank + 1]
    if tail.startswith(">   "):
        # Tail is another row — no levelup section.
        return body, "", ""
    streak_part = "\n".join(lines[:last_blank])
    levelup_part = "\n".join(lines[last_blank + 1:])
    return streak_part, "", levelup_part
