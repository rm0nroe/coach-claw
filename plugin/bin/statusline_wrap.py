#!/usr/bin/env python3
"""Wrap the user's existing statusLine command and append the Coach segment.

Reads the JSON payload from stdin, runs the user's saved original command
with the same payload (subprocess + 2s timeout), then composes the
original output with `stats.render_segment(payload)` using trailing-aware
separator detection.

Saved-original lookup: `<coach_dir>/.statusline-wrap.json` →
`{"original_command": "<verbatim user command string>"}`. Written by
`statusline_wrap_action.wrap()` before settings.json is mutated.

Composer rules (separator-aware, ANSI-stripped):
  1. If original ends with a known separator char → append with one space.
  2. Else if an inline separator pattern (` X `) exists → reuse it.
  3. Else → join with a single space.

Runtime duplicate-detection: if the (ANSI-stripped) original output already
contains a Coach signature (sigil glyph + roman numeral OR theme rank
name within ~30 chars), the wrapper writes
`<coach_dir>/.statusline-wrap-duplicate-detected` and emits the original
alone. The next user prompt surfaces a banner suggesting `--unwrap-statusline`.

Failsafe: every exception path emits the captured original (or empty
string) and exits 0. The statusline must never crash a render.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coach_paths import resolve_coach_dir  # noqa: E402

# Lazy-import stats only inside the rendering path so a broken stats.py
# can't take down the wrapper before it's even tried to run the user's
# command (failsafe-first design).

WRAP_MARKER_NAME = ".statusline-wrap.json"
DUPLICATE_MARKER_NAME = ".statusline-wrap-duplicate-detected"
ORIGINAL_TIMEOUT_SECONDS = 2.0

ANSI_RE = re.compile(r"\x1b\[[\d;]*m")
SEPARATOR_CHARS = "┃│|·•‣→›—-"

# Coach-signature regex for duplicate detection. Requires a sigil glyph
# (◆ / ⚒ / ⚔) within ~40 chars of the end, immediately followed by either
# a roman numeral [Ⅰ-Ⅼ]+ or a theme rank-name fragment within ~30 chars.
# The rank-name list is loaded from themes._RANK_NAMES_BY_THEME at runtime
# (lazy-imported to keep startup cheap on the failsafe path).
_DUP_SIGIL_RE = re.compile(r"[◆⚒⚔]")
_DUP_ROMAN_RE = re.compile(r"[◆⚒⚔]\s+[Ⅰ-Ⅼ]+\b")


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _detect_inline_separator(text: str) -> str | None:
    """Most-frequent separator char appearing space-padded (` X `).

    Returns None when no separator-char shows up surrounded by spaces;
    that filters out incidental occurrences like `·` inside `opus·4.7`
    where the chars are squashed against word characters.
    """
    matches = re.findall(rf" ([{re.escape(SEPARATOR_CHARS)}]) ", text)
    if not matches:
        return None
    return Counter(matches).most_common(1)[0][0]


def compose(original: str, coach: str) -> str:
    """Compose original + coach with trailing-aware separator handling.

    Pure function — no I/O, no side effects. The full algorithm lives
    here so it can be exhaustively unit-tested without subprocess
    overhead.
    """
    if not coach:
        return original
    if not original or not original.strip():
        return coach
    stripped = _strip_ansi(original).rstrip()
    if stripped and stripped[-1] in SEPARATOR_CHARS:
        return f"{original.rstrip()} {coach}"
    sep = _detect_inline_separator(stripped)
    if sep:
        return f"{original.rstrip()} {sep} {coach}"
    return f"{original.rstrip()} {coach}"


def _looks_like_coach_output(text: str) -> bool:
    """Detect a Coach segment already present in the original output.

    Two-stage match (the narrowed signature from plan §E fix #6):
      1. Sigil glyph (◆ / ⚒ / ⚔) within last ~40 chars.
      2. Either a roman numeral [Ⅰ-Ⅼ]+ immediately after the sigil, OR a
         theme rank-name within ~30 chars after the sigil.

    Roman-numeral check is fast (regex). Rank-name check imports
    `themes` lazily to avoid bloat on the common no-match path.
    """
    plain = _strip_ansi(text)
    if not plain:
        return False
    tail = plain[-80:]
    sigil_match = _DUP_SIGIL_RE.search(tail)
    if not sigil_match:
        return False
    if _DUP_ROMAN_RE.search(tail):
        return True
    try:
        import themes  # noqa: WPS433 — lazy by design
    except Exception:
        return False
    rank_names: set[str] = set()
    for ladder in getattr(themes, "THEMES", {}).values():
        for name in ladder:
            if isinstance(name, str) and len(name) >= 3:
                rank_names.add(name)
    after_sigil = tail[sigil_match.end():sigil_match.end() + 30]
    return any(name in after_sigil for name in rank_names)


def _read_marker_path() -> Path:
    return resolve_coach_dir() / WRAP_MARKER_NAME


def _read_saved_original() -> str | None:
    """Return the saved original command string, or None if marker
    missing / unparseable. Never raises."""
    try:
        path = _read_marker_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        cmd = data.get("original_command")
        return cmd if isinstance(cmd, str) and cmd else None
    except Exception:
        return None


def _write_duplicate_marker() -> None:
    """Best-effort marker write. The hook reads it on next prompt to
    surface a banner. Never raises.

    Schema matches the consumed-by pattern (`created_at` + `consumed_by`)
    so `_read_and_consume` in the hook handles per-session dedup + TTL."""
    try:
        coach_dir = resolve_coach_dir()
        coach_dir.mkdir(parents=True, exist_ok=True)
        marker = coach_dir / DUPLICATE_MARKER_NAME
        if not marker.exists():
            marker.write_text(json.dumps({
                "created_at": _utc_now_iso(),
                "consumed_by": [],
            }))
    except Exception:
        pass


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_original(command: str, payload_raw: str) -> str:
    """Run the user's original command with the stdin payload forwarded.

    Returns its stdout on success (zero exit, within timeout), else "".
    Stderr is discarded — the statusline must stay quiet.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            input=payload_raw,
            capture_output=True,
            timeout=ORIGINAL_TIMEOUT_SECONDS,
            text=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout or ""
    except Exception:
        return ""


def _parse_payload(raw: str) -> dict:
    try:
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _coach_segment(payload: dict) -> str:
    try:
        from stats import render_segment  # noqa: WPS433 — lazy
        return render_segment(payload) or ""
    except Exception:
        return ""


def main() -> int:
    original = ""
    try:
        try:
            payload_raw = "" if sys.stdin.isatty() else sys.stdin.read()
        except Exception:
            payload_raw = ""

        saved = _read_saved_original()
        if saved is not None:
            original = _run_original(saved, payload_raw)

        payload = _parse_payload(payload_raw)
        coach = _coach_segment(payload)

        if original and _looks_like_coach_output(original):
            _write_duplicate_marker()
            sys.stdout.write(original)
            return 0

        sys.stdout.write(compose(original, coach))
    except Exception:
        # Last-resort failsafe: emit whatever we captured (or nothing).
        try:
            sys.stdout.write(original)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
