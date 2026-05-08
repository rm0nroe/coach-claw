#!/usr/bin/env python3
"""coach-claw config — terminal-side entrypoint to user_config.

Three subcommands:

    coach-claw config set [--theme NAME] [--statusline VARIANT] [--elo MIN MAX]
    coach-claw config preview
    coach-claw config wizard

Wired up by `npm/coach-claw.js` (case "config" → spawnSync python3
against this file). Inside Claude Code the equivalent surface is the
`/config` slash command at `skills/config/SKILL.md` — both write to the
same `.user_config.json` via `user_config.save()`.

The wizard is intentionally narrow: variant + theme only. ELO range is
a power-user knob; surface it through `config set --elo MIN MAX`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python3 ~/.claude/coach/bin/configure.py …` AND in-repo runs.
_BIN_DIR = Path(__file__).resolve().parent
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

import user_config  # noqa: E402
from statusline_variants import VARIANTS, Glyphs, render  # noqa: E402
from themes import THEMES, list_themes  # noqa: E402


def _sample_glyphs(theme_name: str, level: int = 7) -> Glyphs:
    """Build a representative `Glyphs` payload for previews. Matches the
    payload `/config preview` uses so terminal and slash-command output
    stay visually consistent."""
    ladder = THEMES.get(theme_name, THEMES["craft"])
    return Glyphs(
        level=level,
        name=ladder[level - 1],
        elo=1232,
        session_xp=15,
        sigil_tier="silver",
        bar_pct=0.30,
    )


# --- preview --------------------------------------------------------------

def cmd_preview(_args: argparse.Namespace) -> int:
    """Render every variant × the user's current theme, plus L1/L25/L50
    sample names per theme. Byte-equivalent to `/config preview` from
    inside Claude Code."""
    cfg = user_config.load()
    sample = _sample_glyphs(cfg["theme"])

    print("STATUSLINE VARIANTS (rendered with your current theme):")
    for k in VARIANTS:
        marker = " ← current" if k == cfg["statusline_variant"] else ""
        print(f"  {k:>8} → {render(k, sample)}{marker}")

    print()
    print("THEMES (sample L1 / L25 / L50 names):")
    for name in list_themes():
        arr = THEMES[name]
        marker = " ← current" if name == cfg["theme"] else ""
        print(f"  {name:>13} → {arr[0]} … {arr[24]} … {arr[49]}{marker}")
    return 0


# --- set ------------------------------------------------------------------

def cmd_set(args: argparse.Namespace) -> int:
    """Apply explicitly-passed flags to the config. Keys not passed stay
    at their existing value (delegated to `user_config.update()`)."""
    updates: dict = {}
    if args.theme is not None:
        updates["theme"] = args.theme
    if args.statusline is not None:
        updates["statusline_variant"] = args.statusline
    if args.elo is not None:
        emin, emax = args.elo
        updates["elo_min"] = emin
        updates["elo_max"] = emax

    if not updates:
        print("nothing to do — pass at least one of --theme / --statusline / --elo",
              file=sys.stderr)
        print("(or run `coach-claw config preview` to see what's available)",
              file=sys.stderr)
        return 1

    try:
        user_config.update(**updates)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = ", ".join(f"{k}={v}" for k, v in updates.items())
    print(f"saved: {summary}")
    print("Open a new Claude Code prompt to see it.")
    return 0


# --- wizard ---------------------------------------------------------------

def _ask_choice(label: str, choices: list[str], current: str) -> str:
    """Prompt for a value from `choices`, defaulting to `current` on
    blank input. Re-prompts on invalid input. After 3 invalid tries,
    falls through with the default to avoid infinite loops in weird
    terminal contexts."""
    print()
    print(f"{label} (current: {current}):")
    for i, name in enumerate(choices, start=1):
        marker = "  ←" if name == current else ""
        print(f"  {i:>2}. {name}{marker}")
    print(f"  (Enter to keep '{current}')")

    for attempt in range(3):
        raw = input("> ").strip()
        if not raw:
            return current
        # Numeric pick
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(choices):
                return choices[idx - 1]
        # Name pick
        if raw in choices:
            return raw
        print(f"  '{raw}' is not a valid choice. Pick a number 1-{len(choices)} or a name from the list.")

    print(f"  (giving up after 3 invalid tries — keeping '{current}')")
    return current


def cmd_wizard(_args: argparse.Namespace) -> int:
    """Interactive variant + theme picker. TTY-gated; non-interactive
    invocations get a pointer to `config set` and exit 0."""
    if not sys.stdin.isatty():
        print("Wizard requires an interactive terminal.")
        print("For scripted/CI installs, use:")
        print("  coach-claw config set --theme ocean --statusline pips")
        return 0

    cfg = user_config.load()

    # Show the live preview up front so the user has a visual reference
    # for the choices to come.
    cmd_preview(_args)

    try:
        new_variant = _ask_choice(
            "Pick a statusline variant",
            list(VARIANTS.keys()),
            cfg["statusline_variant"],
        )
        new_theme = _ask_choice(
            "Pick a theme (50-name level ladder + celebration banners)",
            list_themes(),
            cfg["theme"],
        )
    except (KeyboardInterrupt, EOFError):
        print()
        print("Wizard cancelled — no changes saved.")
        return 0

    if new_variant == cfg["statusline_variant"] and new_theme == cfg["theme"]:
        print()
        print("No changes — config left untouched.")
        return 0

    try:
        user_config.update(
            statusline_variant=new_variant,
            theme=new_theme,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    sample = _sample_glyphs(new_theme)
    print(f"  saved: {render(new_variant, sample)}")
    print("Open a new Claude Code prompt to see it.")
    return 0


# --- argparse glue --------------------------------------------------------

def _parse_elo(raw: str) -> list[int]:
    """argparse type for `--elo MIN MAX`. Accepts two whitespace-separated
    ints; argparse runs this for each token with nargs=2."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"ELO bound must be an integer, got {raw!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(f"ELO bounds must be positive, got {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coach-claw config",
        description="Terminal-side editor for ~/.claude/coach/.user_config.json. "
                    "Same backing file as the /config slash command in Claude Code.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Apply specific values without prompting")
    p_set.add_argument("--theme", help=f"one of: {', '.join(list_themes())}")
    p_set.add_argument(
        "--statusline",
        help=f"one of: {', '.join(VARIANTS.keys())}",
    )
    p_set.add_argument(
        "--elo",
        nargs=2,
        type=_parse_elo,
        metavar=("MIN", "MAX"),
        help="ELO interpolation range (default 1000 2800)",
    )
    p_set.set_defaults(func=cmd_set)

    p_preview = sub.add_parser(
        "preview", help="Print every variant × theme combo so you can pick one")
    p_preview.set_defaults(func=cmd_preview)

    p_wizard = sub.add_parser(
        "wizard", help="Interactive picker for variant + theme (TTY only)")
    p_wizard.set_defaults(func=cmd_wizard)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
