#!/usr/bin/env python3
"""Render the default Coach Claw statusline composition:

    ◆ <model> ┃ <bar> NN% ┃ <coach segment>

Reads the Claude Code statusline JSON from stdin once and emits a
single ANSI-colored line on stdout. Replaces an earlier bash wrapper
that piped through `jq` twice — jq is not in macOS's default PATH,
so a fresh box rendered `jq: command not found` instead of a model
name and 0% instead of a bar.

Visual contract — must match what the v0.3.0 bash wrapper rendered:

  • ICE_SILVER ◆ + lowercased model name with " context" stripped and
    spaces collapsed to "·"
  • DEEP_COBALT ┃ separator
  • 20-segment context-window bar with white→cobalt gradient (filled)
    and DIM_STEEL ▱ (empty); int-clamped 0-100; round-half-up of float
    percentage
  • ICE_SILVER NN%
  • DEEP_COBALT ┃ separator
  • coach segment from stats.render_segment() (level + ELO + session
    arrow); rendered in-process — no second Python invocation

The prefix always renders even on a fresh install with no profile;
the trailing coach segment is silent until stats.py has signal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import render_segment  # noqa: E402

# Palette — RGB triples match coach/default-statusline-command.sh.
ICE_SILVER  = "\x1b[38;2;200;214;229m"
DEEP_COBALT = "\x1b[38;2;30;144;255m"
DIM_STEEL   = "\x1b[38;2;58;58;74m"
RESET       = "\x1b[0m"

BAR_SEGMENTS = 20


def _normalize_model(display_name: str) -> str:
    """Lowercase, strip ' context' substrings, collapse spaces to '·'.

    Mirrors `tr '[:upper:]' '[:lower:]' | sed 's/ context//g; s/ /·/g'`
    from the deleted bash wrapper. Order matters: lowercase first so
    "Context" matches " context" after casing.
    """
    s = display_name.lower().replace(" context", "")
    return s.replace(" ", "·")


def _render_bar(used_int: int) -> str:
    """20-segment bar with white→cobalt gradient on filled segments."""
    used_int = max(0, min(100, used_int))
    filled = used_int * BAR_SEGMENTS // 100
    empty = BAR_SEGMENTS - filled
    parts: list[str] = []
    for i in range(filled):
        if filled <= 1:
            t_num, t_den = 0, 1
        else:
            t_num, t_den = i, filled - 1
        # White (#FFFFFF) → DEEP_COBALT (#1E90FF). Integer math matches
        # bash's `$(( ))` rounding-toward-zero so visual output is
        # byte-identical to the deleted wrapper for any used_int.
        r = 255 - (255 - 30)  * t_num // t_den
        g = 255 - (255 - 144) * t_num // t_den
        b = 255
        parts.append(f"\x1b[38;2;{r};{g};{b}m▰{RESET}")
    parts.extend(f"{DIM_STEEL}▱{RESET}" for _ in range(empty))
    return "".join(parts)


def _read_stdin_payload() -> dict:
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


def _safe_get(d: dict, *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def main() -> int:
    try:
        payload = _read_stdin_payload()

        raw_name = _safe_get(payload, "model", "display_name")
        display_name = raw_name if isinstance(raw_name, str) and raw_name else "unknown"
        model_label = _normalize_model(display_name)

        raw_pct = _safe_get(payload, "context_window", "used_percentage")
        used_pct = float(raw_pct) if isinstance(raw_pct, (int, float)) else 0.0
        used_int = max(0, min(100, int(round(used_pct))))

        out = (
            f"{ICE_SILVER}◆ {model_label}{RESET} {DEEP_COBALT}┃{RESET} "
            f"{_render_bar(used_int)} {ICE_SILVER}{used_int}%{RESET} "
            f"{DEEP_COBALT}┃{RESET}"
        )

        coach = render_segment(payload)
        if coach:
            out = f"{out} {coach}"

        sys.stdout.write(out)
    except Exception:
        # Failsafe: the statusline runs on every render. A crash here
        # must not noise up the terminal with a traceback or break
        # Claude Code's render. Match the hook fail-soft contract —
        # emit nothing and exit 0; the user's native statusline (or
        # none) takes over for the next render.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
