"""statusline_wrap.py — runtime composer with ANSI-strip + trailing-aware
separator detection. Failsafe contract: never crash the render."""
from __future__ import annotations

import json
import sys
import io
from pathlib import Path

import pytest

import statusline_wrap as sw


# --- compose() pure function ----------------------------------------------


def test_compose_appends_when_original_ends_with_separator():
    """User's command already trails with `┃` → just append, single space."""
    out = sw.compose("opus·4.7 ┃ ▰▰▱▱ 14% ┃", "◆ Ⅷ 1265 Sensei ↑1")
    assert out == "opus·4.7 ┃ ▰▰▱▱ 14% ┃ ◆ Ⅷ 1265 Sensei ↑1"


def test_compose_strips_ansi_before_trailing_check():
    """Real terminal output has ANSI color codes wrapped around the `┃`.
    Last raw char is `m`; after ANSI-strip, last visible char is `┃`."""
    ansi_pipe = "\x1b[38;2;30;144;255m┃\x1b[0m"
    original = f"opus·4.7 \x1b[38;2;30;144;255m┃\x1b[0m ▰▰▱▱ 14% {ansi_pipe}"
    out = sw.compose(original, "COACH")
    # No double separator inserted — trailing `┃` was detected post-strip.
    assert out.endswith(" COACH")
    assert " ┃ COACH" not in out  # would be doubled


def test_compose_uses_inline_separator_when_no_trailing_one():
    """No trailing sep, but inline ` | ` runs around → insert one."""
    out = sw.compose("a | b | c", "COACH")
    assert out == "a | b | c | COACH"


def test_compose_falls_through_to_space_when_no_separator_signal():
    """Nothing recognizable → single-space join."""
    out = sw.compose("plain text", "COACH")
    assert out == "plain text COACH"


def test_compose_empty_original_returns_coach_alone():
    assert sw.compose("", "COACH") == "COACH"
    assert sw.compose("   ", "COACH") == "COACH"


def test_compose_empty_coach_returns_original_alone():
    assert sw.compose("original ┃", "") == "original ┃"


# --- inline-separator detection edge cases ---------------------------------


def test_detect_inline_separator_picks_most_frequent():
    assert sw._detect_inline_separator("a ┃ b ┃ c | d") == "┃"


def test_detect_inline_separator_returns_none_when_squashed():
    """`opus·4.7·(1m)` — `·` between alphanumerics, not space-padded.
    Must not be picked as a separator."""
    assert sw._detect_inline_separator("opus·4.7·(1m)") is None


def test_detect_inline_separator_returns_none_on_no_separators():
    assert sw._detect_inline_separator("plain text only") is None


# --- runtime duplicate-detection signature --------------------------------


def test_looks_like_coach_output_matches_sigil_plus_roman():
    """`◆ Ⅷ` — sigil + roman within last 80 chars → duplicate signature."""
    assert sw._looks_like_coach_output("model bar ┃ ◆ Ⅷ 1265 Sensei ↑1") is True


def test_looks_like_coach_output_matches_sigil_plus_rank_name():
    """`⚒ Virtuoso` — sigil + theme rank name → duplicate signature."""
    assert sw._looks_like_coach_output("custom prefix ⚒ Virtuoso · L7 ↑15") is True


def test_looks_like_coach_output_no_match_on_unrelated():
    """No sigil → no match."""
    assert sw._looks_like_coach_output("just plain text 14%") is False


def test_looks_like_coach_output_no_match_on_sigil_alone():
    """Sigil present but no roman + no rank → not a Coach signature."""
    assert sw._looks_like_coach_output("symbol ◆ alone with no rank") is False


def test_looks_like_coach_output_strips_ansi_first():
    """ANSI colors around the sigil/numeral mustn't break detection."""
    text = "x \x1b[38;2;200;205;215m◆\x1b[0m \x1b[1mⅧ\x1b[0m 1265"
    assert sw._looks_like_coach_output(text) is True


# --- main() integration with subprocess ------------------------------------


@pytest.fixture
def isolated_coach_dir(tmp_path, monkeypatch):
    """Redirect resolve_coach_dir() to a tmp dir."""
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_marker(coach_dir: Path, command: str) -> None:
    (coach_dir / sw.WRAP_MARKER_NAME).write_text(json.dumps({
        "original_command": command,
    }))


def _run_main(monkeypatch, capsys, payload: str = ""):
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    rc = sw.main()
    return rc, capsys.readouterr().out


def test_main_no_marker_emits_coach_alone(isolated_coach_dir, monkeypatch, capsys):
    """Without a saved-original marker, the wrapper has nothing to run.
    It still composes — original is empty, so just the coach segment
    emits (or empty if no profile)."""
    rc, out = _run_main(monkeypatch, capsys, payload="")
    assert rc == 0
    # Either empty (no profile signal) or starts with a sigil; both are
    # valid failsafe outcomes. Key invariant: no traceback, no crash.
    assert isinstance(out, str)


def test_main_runs_saved_command_and_appends_coach(
    isolated_coach_dir, monkeypatch, capsys, tmp_path
):
    """Saved original is `echo 'orig ┃'` → wrapper appends Coach with
    trailing-aware logic (already has trailing ┃)."""
    _write_marker(isolated_coach_dir, "printf 'orig ┃'")
    monkeypatch.setattr(sw, "_coach_segment", lambda payload: "COACH")

    rc, out = _run_main(monkeypatch, capsys, payload="")
    assert rc == 0
    assert out == "orig ┃ COACH"


def test_main_timeout_emits_coach_alone(isolated_coach_dir, monkeypatch, capsys):
    """Saved command sleeps past the 2s budget → original captured as ""
    → wrapper falls through to coach-alone."""
    _write_marker(isolated_coach_dir, "sleep 5")
    monkeypatch.setattr(sw, "ORIGINAL_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(sw, "_coach_segment", lambda payload: "COACH")

    rc, out = _run_main(monkeypatch, capsys, payload="")
    assert rc == 0
    assert out == "COACH"


def test_main_invalid_payload_does_not_crash(isolated_coach_dir, monkeypatch, capsys):
    """Garbage stdin → parsed as empty dict; wrapper still emits."""
    _write_marker(isolated_coach_dir, "printf 'orig'")
    monkeypatch.setattr(sw, "_coach_segment", lambda payload: "COACH")

    rc, out = _run_main(monkeypatch, capsys, payload="not json {{{")
    assert rc == 0
    assert "orig" in out


def test_main_duplicate_detection_writes_marker_and_skips_coach(
    isolated_coach_dir, monkeypatch, capsys
):
    """Original output already contains a Coach signature → the wrapper
    must NOT append a second segment AND must drop a marker file for the
    hook to surface the suggestion banner."""
    _write_marker(isolated_coach_dir, "printf '◆ Ⅷ 1265 Sensei ↑1'")
    monkeypatch.setattr(sw, "_coach_segment", lambda payload: "COACH")

    rc, out = _run_main(monkeypatch, capsys, payload="")
    assert rc == 0
    assert "COACH" not in out
    assert out == "◆ Ⅷ 1265 Sensei ↑1"
    marker = isolated_coach_dir / sw.DUPLICATE_MARKER_NAME
    assert marker.exists()


def test_main_subprocess_failure_falls_back_to_coach(
    isolated_coach_dir, monkeypatch, capsys
):
    """Saved command exits nonzero → original captured as "" → coach alone."""
    _write_marker(isolated_coach_dir, "false")
    monkeypatch.setattr(sw, "_coach_segment", lambda payload: "COACH")

    rc, out = _run_main(monkeypatch, capsys, payload="")
    assert rc == 0
    assert out == "COACH"
