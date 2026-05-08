"""Statusline render variants — five compact one-liners that occupy the
trailing slot in Claude Code's status row.

Each variant takes a `Glyphs` payload (level index, level name, ELO,
session XP gain, sigil color tier — "bronze"/"silver"/"gold"/"platinum"/
"diamond") and returns a single ANSI-colored string ≤ ~30 visible chars.

The selected variant is read from `~/.claude/coach/.user_config.json`
via `user_config.get_variant()`. Default = "crystal" (preserves the
v0.2.0 look).

Adding a variant: append a render function and register it in VARIANTS.
The `/config` slash command lists keys from VARIANTS so users can see
them without re-reading source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# --- ANSI palette (24-bit RGB) -------------------------------------------
# Shared with the rest of the coach renderer + the user's statusline. Keep
# in sync with stats.py if changed.
ICE_SILVER  = "\x1b[38;2;200;214;229m"
DIM_STEEL   = "\x1b[38;2;58;58;74m"
MUTED_STEEL = "\x1b[38;2;110;122;140m"
RESET       = "\x1b[0m"
GAIN_EMERALD = "\x1b[38;2;90;218;170m"
BOLD = "\x1b[1m"

SIGIL_COLORS = {
    "bronze":   "\x1b[38;2;205;127;50m",
    "silver":   "\x1b[38;2;200;205;215m",
    "gold":     "\x1b[38;2;245;197;66m",
    "platinum": "\x1b[38;2;180;220;235m",
    "diamond":  "\x1b[38;2;150;240;255m",
}

# Roman numeral converter (concatenated form for readability past Ⅹ).
_ROMAN_TENS  = ["", "Ⅹ", "ⅩⅩ", "ⅩⅩⅩ", "ⅩⅬ", "Ⅼ"]
_ROMAN_UNITS = ["", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ"]

def to_roman(n: int) -> str:
    n = max(1, min(n, 50))
    return _ROMAN_TENS[n // 10] + _ROMAN_UNITS[n % 10]


@dataclass(frozen=True)
class Glyphs:
    """Render-time payload — pre-computed by stats.py and passed verbatim
    to a variant function. No business logic in variants."""
    level: int           # 1-indexed (L1..L50)
    name: str            # theme-resolved level name
    elo: int             # 4-digit rating, e.g. 1232
    session_xp: int      # raw session XP, 0..15 (capped)
    sigil_tier: str      # "bronze"/"silver"/"gold"/"platinum"/"diamond"
    bar_pct: float       # 0..1 within-level progress (used by pip-bar variants)


def _gain(g: Glyphs) -> str:
    """Trailing ↑N session-gain arrow. Empty when session_xp == 0."""
    return f" {GAIN_EMERALD}↑{g.session_xp}{RESET}" if g.session_xp > 0 else ""


def _sigil_color(tier: str) -> str:
    return SIGIL_COLORS.get(tier, SIGIL_COLORS["bronze"])


# === VARIANTS ============================================================
# Each variant returns a ready-to-print ANSI string.

def render_crystal(g: Glyphs) -> str:
    """The v0.2.0 default. Sigil + roman + ELO + name + arrow.

        ◆ Ⅶ 1232 Virtuoso ↑15
    """
    sigil = f"{_sigil_color(g.sigil_tier)}◆{RESET}"
    roman = f"{BOLD}{ICE_SILVER}{to_roman(g.level)}{RESET}"
    elo = f"{ICE_SILVER}{g.elo:04d}{RESET}"
    name = f"{MUTED_STEEL}{g.name}{RESET}"
    return f"{sigil} {roman} {elo} {name}{_gain(g)}"


def render_pips(g: Glyphs) -> str:
    """Lead with within-level progress as a 10-pip bar.

        ●●●●●●●●○○ Virtuoso ↑15
    """
    filled = max(0, min(10, round(g.bar_pct * 10)))
    pips = (
        f"{ICE_SILVER}{'●' * filled}{RESET}"
        f"{DIM_STEEL}{'○' * (10 - filled)}{RESET}"
    )
    name = f"{BOLD}{ICE_SILVER}{g.name}{RESET}"
    return f"{pips} {name}{_gain(g)}"


def render_bracket(g: Glyphs) -> str:
    """Typographic. Level + name in brackets, ELO middle, arrow trail.

        [Ⅶ Virtuoso] 1232 ↑15
    """
    inside = f"{BOLD}{ICE_SILVER}{to_roman(g.level)} {g.name}{RESET}"
    bracket_open  = f"{MUTED_STEEL}[{RESET}"
    bracket_close = f"{MUTED_STEEL}]{RESET}"
    elo = f"{ICE_SILVER}{g.elo:04d}{RESET}"
    return f"{bracket_open}{inside}{bracket_close} {elo}{_gain(g)}"


def render_slash(g: Glyphs) -> str:
    """Path-style separators, terse and dev-shell aesthetic.

        L7 / Virtuoso / 1232 ↑15
    """
    sep = f"{MUTED_STEEL}/{RESET}"
    lvl = f"{BOLD}{ICE_SILVER}L{g.level}{RESET}"
    name = f"{ICE_SILVER}{g.name}{RESET}"
    elo = f"{ICE_SILVER}{g.elo:04d}{RESET}"
    return f"{lvl} {sep} {name} {sep} {elo}{_gain(g)}"


def render_forge(g: Glyphs) -> str:
    """Anvil-themed sigil, name leads, level + ELO follow.

        ⚒ Virtuoso · L7 · 1232 ↑15
    """
    sigil = f"{_sigil_color(g.sigil_tier)}⚒{RESET}"
    name = f"{BOLD}{ICE_SILVER}{g.name}{RESET}"
    sep = f"{MUTED_STEEL}·{RESET}"
    lvl = f"{ICE_SILVER}L{g.level}{RESET}"
    elo = f"{ICE_SILVER}{g.elo:04d}{RESET}"
    return f"{sigil} {name} {sep} {lvl} {sep} {elo}{_gain(g)}"


# === REGISTRY ============================================================
VARIANTS: dict[str, Callable[[Glyphs], str]] = {
    "crystal": render_crystal,
    "pips":    render_pips,
    "bracket": render_bracket,
    "slash":   render_slash,
    "forge":   render_forge,
}

DEFAULT_VARIANT = "crystal"


def render(variant: str, glyphs: Glyphs) -> str:
    """Dispatch to the selected variant. Falls back to default on unknown
    keys — caller never sees a KeyError."""
    fn = VARIANTS.get(variant, VARIANTS[DEFAULT_VARIANT])
    return fn(glyphs)


def list_variants() -> list[str]:
    """Stable list of variant keys, default first for `/config`."""
    return [DEFAULT_VARIANT] + sorted(k for k in VARIANTS if k != DEFAULT_VARIANT)
