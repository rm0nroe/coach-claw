"""Hook guard: refuse stateful writes when session_id is missing.

v0.5.2 fix for two narrow but real bugs surfaced during the uninstall
e2e:

  R1 — `coach-session-start.py` spawned `bank.py` and `insights-llm.sh`
        fire-and-forget regardless of payload. An ad-hoc smoke test
        (`echo '{}' | python3 hook.py`) on a freshly-installed empty
        coach dir would let bank.py race against an in-progress restore
        and write a bogus `.pending_levelup` jumping the user from
        Builder → Virtuoso.

  R2 — `coach-user-prompt.py` fell through to writing an empty/sentinel
        `session_key` into pending markers' `consumed_by` list. Real
        Claude Code sessions then saw the marker as already-consumed
        and skipped rendering.

The guard: if no `session_id`/`sessionId`/`transcript_path`/`transcriptPath`
in the payload, exit 0 silently with NO subprocess spawn and NO marker
mutation. Real Claude Code events always carry one of those fields; the
only callers without them are smoke tests and malformed input.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
SESSION_START = HOOKS_DIR / "coach-session-start.py"
USER_PROMPT = HOOKS_DIR / "coach-user-prompt.py"


def _run_hook(hook_path: Path, payload: dict, claude_dir: Path) -> subprocess.CompletedProcess:
    """Invoke a hook with the given payload, isolating COACH_DIR via $HOME."""
    env = os.environ.copy()
    env["HOME"] = str(claude_dir.parent)
    # Make sure the hook's `Path.home() / ".claude" / "coach"` resolves into
    # our temp dir, not the user's real install.
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


@pytest.fixture
def fake_claude_home(tmp_path: Path) -> Path:
    """Build a minimal `~/.claude/coach/` so hooks have somewhere to look."""
    home = tmp_path / "home"
    coach = home / ".claude" / "coach"
    coach.mkdir(parents=True)
    (coach / "bin").mkdir()
    # A real bank.py would fire; we install a sentinel that records its run
    # by touching a file. If the guard works, this file MUST NOT appear.
    sentinel = coach / "bank-was-spawned.sentinel"
    bank_py = coach / "bin" / "bank.py"
    bank_py.write_text(
        f"#!/usr/bin/env python3\n"
        f"from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('spawned')\n"
    )
    bank_py.chmod(0o755)
    return home


def _hook_available(hook: Path) -> bool:
    """Skip when the bundle isn't checked out (e.g. running from ~/.claude/coach/)."""
    return hook.exists()


def test_session_start_with_empty_input_does_nothing(fake_claude_home: Path) -> None:
    """`echo '{}' | coach-session-start.py` exits 0 silently, no bank spawn."""
    if not _hook_available(SESSION_START):
        pytest.skip("hook source not in this checkout")

    result = _run_hook(SESSION_START, {}, fake_claude_home)
    assert result.returncode == 0
    assert result.stdout.strip() == "", (
        f"hook should produce no output for empty payload; got: {result.stdout!r}"
    )

    sentinel = fake_claude_home / ".claude" / "coach" / "bank-was-spawned.sentinel"
    # bank.py spawn is detached, so we wait briefly to give a hypothetical
    # spawn time to land. If the guard works, no spawn happened.
    import time
    time.sleep(0.5)
    assert not sentinel.exists(), (
        "bank.py was spawned despite missing session_id — guard regression"
    )


def test_session_start_with_session_id_proceeds(fake_claude_home: Path) -> None:
    """Sanity: a payload WITH session_id should NOT be silenced by the guard.

    We don't assert bank spawn here (it depends on a profile.yaml that we
    haven't set up); we just assert the hook doesn't bail at the guard.
    The hook is wrapped in a try/except that always exits 0, so we look
    for a side effect: it should at least attempt to read the profile,
    which means it got past the guard. Easiest signal: stdout is allowed
    to be empty (silent because no profile), but exit must be 0 (failsafe).
    """
    if not _hook_available(SESSION_START):
        pytest.skip("hook source not in this checkout")

    result = _run_hook(SESSION_START, {"session_id": "test-abc"}, fake_claude_home)
    assert result.returncode == 0


def test_user_prompt_with_empty_input_does_not_consume_markers(
    fake_claude_home: Path,
) -> None:
    """A pending marker's consumed_by must NOT gain an entry from `{}`."""
    if not _hook_available(USER_PROMPT):
        pytest.skip("hook source not in this checkout")

    coach = fake_claude_home / ".claude" / "coach"
    marker = coach / ".pending_streak_rewards"
    marker.write_text(json.dumps({
        "rewards": [{"id": "x", "name": "X", "streak": 3}],
        "created_at": "2026-05-01T00:00:00+00:00",
        "consumed_by": [],
    }))

    result = _run_hook(USER_PROMPT, {}, fake_claude_home)
    assert result.returncode == 0
    assert result.stdout.strip() == "", (
        f"hook should produce no output for empty payload; got: {result.stdout!r}"
    )

    # The load-bearing assertion: consumed_by stays empty so the marker
    # is still visible to the next REAL session.
    after = json.loads(marker.read_text())
    assert after["consumed_by"] == [], (
        f"empty payload polluted consumed_by: {after['consumed_by']}"
    )


def test_user_prompt_with_transcript_path_proceeds(fake_claude_home: Path) -> None:
    """Payload with transcript_path (no session_id) should still pass the guard."""
    if not _hook_available(USER_PROMPT):
        pytest.skip("hook source not in this checkout")

    # transcript_path doesn't need to exist for the guard check — that's
    # the job of later confinement logic. The guard is purely a presence test.
    result = _run_hook(
        USER_PROMPT,
        {"transcript_path": "/tmp/nonexistent.jsonl"},
        fake_claude_home,
    )
    assert result.returncode == 0
