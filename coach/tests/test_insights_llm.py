"""Integration tests for coach/bin/insights-llm.sh.

Exercise the actual bash wrapper via subprocess (not just the Python helpers
it composes) so the shell→Python boundary is covered. Uses
COACH_INSIGHTS_LLM_SKIP_REFRESH=1 to bypass the `claude -p /insights`
subprocess and operate on a fixture facets directory.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "insights-llm.sh"
BIN_DIR = SCRIPT.parent


def _seed_coach_dir(tmp_path: Path) -> Path:
    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()
    profile = {
        "schema_version": 1,
        "updated": None,
        "entries": [],
        "recent_runs": [],
    }
    (coach_dir / "profile.yaml").write_text(yaml.safe_dump(profile))
    (coach_dir / "changelog.md").touch()
    subprocess.run(["git", "init", "-q"], cwd=coach_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=coach_dir,
        check=True,
    )
    return coach_dir


def _seed_facets(tmp_path: Path, n: int = 5) -> Path:
    facets = tmp_path / "facets"
    facets.mkdir()
    for i in range(n):
        (facets / f"s{i}.json").write_text(
            json.dumps({
                "session_id": f"s{i}",
                "friction_counts": {"misunderstood_request": 1, "wrong_approach": 1},
                "friction_detail": f"session {i} mislabeled the work and went the wrong direction",
                "primary_success": "good_debugging",
                "brief_summary": f"session {i} drove a bug to root cause",
            })
        )
    return facets


def _run(coach_dir: Path, facets: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "COACH_DIR_OVERRIDE": str(coach_dir),
        "COACH_FACETS_DIR": str(facets),
        "COACH_INSIGHTS_LLM_SKIP_REFRESH": "1",
        # Strip any GIT_* env that might come in from the test runner so the
        # commit step inside the wrapper uses our throwaway coach_dir's git.
        "GIT_DIR": "",
        "GIT_WORK_TREE": "",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_run_id_prefix_distinguishes_weekly(tmp_path: Path) -> None:
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    result = _run(coach_dir, facets)
    assert result.returncode == 0, result.stderr
    assert "run_id=insights-weekly-" in result.stdout
    # Profile entries get a source_runs entry with the weekly prefix.
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    entries = profile.get("entries") or []
    assert entries, "expected merge to land at least one entry"
    for e in entries:
        for run in (e.get("source_runs") or []):
            assert run.startswith("insights-weekly-"), run


def test_throttle_marker_set_on_success(tmp_path: Path) -> None:
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    marker = coach_dir / ".last_weekly_insights"
    assert not marker.exists()
    result = _run(coach_dir, facets)
    assert result.returncode == 0, result.stderr
    assert marker.exists(), "throttle marker was not touched"


def test_throttle_skips_recent_run(tmp_path: Path) -> None:
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    # First run lands the marker.
    r1 = _run(coach_dir, facets)
    assert r1.returncode == 0
    # Second run immediately after should skip.
    r2 = _run(coach_dir, facets)
    assert r2.returncode == 0
    assert "skipped" in r2.stdout.lower()
    # And should NOT have run merge again — recent_runs should be length 1.
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    assert len(profile.get("recent_runs") or []) == 1


def test_force_overrides_cooldown(tmp_path: Path) -> None:
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    r1 = _run(coach_dir, facets)
    assert r1.returncode == 0
    r2 = _run(coach_dir, facets, "--force")
    assert r2.returncode == 0
    assert "skipped" not in r2.stdout.lower()
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    # Two successful merges → two entries in recent_runs.
    assert len(profile.get("recent_runs") or []) == 2


def test_dry_run_skips_merge(tmp_path: Path) -> None:
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    marker = coach_dir / ".last_weekly_insights"
    result = _run(coach_dir, facets, "--dry-run")
    assert result.returncode == 0, result.stderr
    # Profile untouched.
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    assert profile.get("entries") == []
    assert profile.get("recent_runs") == []
    # Marker untouched.
    assert not marker.exists()
    # Detections JSON printed.
    assert "(dry-run; merge skipped" in result.stdout
    # The aggregator's JSON list embedded in stdout.
    assert "misunderstood-request" in result.stdout


def test_invalid_facets_dir_bails_on_no_evidence(tmp_path: Path) -> None:
    """A nonexistent facets dir is "no current-window evidence" — wrapper
    must bail with exit 7 (v0.5.1 evidence gate). Pre-v0.5.1 this used
    to merge `[]` as a clean evidence pass; that was the bug class
    closed by the n_sessions==0 gate."""
    coach_dir = _seed_coach_dir(tmp_path)
    nonexistent = tmp_path / "no-facets-here"
    result = _run(coach_dir, nonexistent)
    assert result.returncode == 7, (
        f"expected exit 7 (no evidence), got {result.returncode}\n"
        f"stderr={result.stderr}"
    )
    assert "no current-window evidence" in result.stderr
    # Profile MUST NOT have advanced — no merge ran.
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    assert profile.get("entries") == []
    assert profile.get("recent_runs") in (None, [])
    assert not (coach_dir / ".last_weekly_insights").exists()


def test_below_threshold_emits_zero_detections(tmp_path: Path) -> None:
    """Sparse facets — friction in 1/10 sessions — emits zero detections,
    wrapper still exits 0 and merges (an empty pass is a meaningful signal)."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = tmp_path / "facets"
    facets.mkdir()
    (facets / "s0.json").write_text(json.dumps({
        "session_id": "s0",
        "friction_counts": {"misunderstood_request": 1},
    }))
    for i in range(1, 10):
        (facets / f"s{i}.json").write_text(json.dumps({"session_id": f"s{i}"}))

    result = _run(coach_dir, facets)
    assert result.returncode == 0
    assert "detections=0" in result.stdout
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    assert profile.get("entries") == []


def _build_isolated_bin(
    tmp_path: Path,
    *,
    agg_body: str,
    claude_body: str | None = None,
) -> Path:
    """Build a `bin/` containing the real wrapper + lock helper + merge
    sidecars, but a *test-controlled* aggregate_facets.py.

    Used by the failure-mode tests below to exercise the wrapper's
    error-handling around aggregator behavior without monkeying with
    the real bundle.

    If ``claude_body`` is provided, also writes an executable `claude`
    shim into the same dir; pair with `_sandbox_path_dir(...,
    extra_dir=fake_bin)` so the wrapper picks up the shim instead of
    the host's real `claude` CLI.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in (
        "insights-llm.sh",
        "run_with_lock.py",
        "merge.py",
        "marker_io.py",
        "reward_hints.py",
        "xp_accounting.py",
    ):
        (fake_bin / name).write_text((BIN_DIR / name).read_text())
    for ext in ("sh", "py"):
        for p in fake_bin.glob(f"*.{ext}"):
            p.chmod(0o755)
    (fake_bin / "aggregate_facets.py").write_text(agg_body)
    (fake_bin / "aggregate_facets.py").chmod(0o755)
    if claude_body is not None:
        claude_shim = fake_bin / "claude"
        claude_shim.write_text(claude_body)
        claude_shim.chmod(0o755)
    return fake_bin


def _sandbox_path_dir(tmp_path: Path, *, extra_dir: Path | None = None) -> str:
    """Construct a sandbox PATH that contains the system coreutils
    (/usr/bin:/bin: dirname, mkdir, mktemp, date, git, touch, kill,
    sleep, …) plus pinned python3 and bash symlinks, but DOES NOT
    expose `claude` (which lives elsewhere — e.g. ~/.nvm or Homebrew).

    The wrapper resolves python3 at insights-llm.sh:57 BEFORE checking
    `claude`, then re-execs through `bash` at line 81 and shells out to
    `dirname`, `mktemp`, `date`, `git`, etc. throughout. A naked
    stripped PATH (e.g. PATH="") would fail at python3 resolution with
    exit 2; a PATH with only python3+bash would fail at the next
    `dirname` call. /usr/bin:/bin is the POSIX-standard base where
    `claude` is *not* installed (it's typically in nvm or
    /usr/local/bin), so it's a safe foundation that excludes claude
    by construction.

    Use ``extra_dir`` to layer in a `fake_bin` that contains a `claude`
    shim (or omit it for the missing-claude case).
    """
    sandbox = tmp_path / "sandbox-bin"
    sandbox.mkdir()
    real_python3 = shutil.which("python3")
    real_bash = shutil.which("bash")
    assert real_python3, "host has no python3 — cannot build sandbox PATH"
    assert real_bash, "host has no bash — cannot build sandbox PATH"
    os.symlink(real_python3, sandbox / "python3")
    os.symlink(real_bash, sandbox / "bash")
    parts = [str(sandbox), "/usr/bin", "/bin"]
    if extra_dir is not None:
        parts.insert(0, str(extra_dir))
    else:
        # Guard against a regression where someone exposes the host PATH
        # by accident — the missing-claude test would silently pass.
        assert not shutil.which(
            "claude", path=":".join(parts)
        ), f"sandbox PATH unexpectedly resolves `claude`: {parts}"
    return ":".join(parts)


def _run_with_path(
    *,
    coach_dir: Path,
    facets: Path,
    fake_bin: Path,
    path: str,
    extra_env: dict | None = None,
    args: tuple = ("--force",),
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Invoke the wrapper from ``fake_bin`` with a custom PATH and no
    ``COACH_INSIGHTS_LLM_SKIP_REFRESH`` so the real LLM-step branch
    runs. Used by the LLM-fail-hard tests."""
    env = {
        # Start clean — do NOT inherit the parent PATH, since that
        # would re-introduce the host's real `claude` binary.
        "HOME": os.environ.get("HOME", str(coach_dir.parent)),
        "PATH": path,
        "COACH_DIR_OVERRIDE": str(coach_dir),
        "COACH_FACETS_DIR": str(facets),
        "GIT_DIR": "",
        "GIT_WORK_TREE": "",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(fake_bin / "insights-llm.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_aggregator_failure_bails_before_merge(tmp_path: Path) -> None:
    """If aggregate_facets.py exits nonzero, the wrapper MUST:
      - exit nonzero itself (not silently treat empty $DET as `[]`)
      - not run merge.py (profile + changelog unchanged)
      - not touch the throttle marker (so the next session can retry)

    Guards the shell→Python boundary documented in
    feedback_test_gap_shell_helper_boundary.md and the P1 caught in
    teammate review: the original `... > $DET` redirect lost the
    aggregator's nonzero exit code and the inline `try: print(len(...))
    except: print(0)` heredoc swallowed JSON parse errors, so a busted
    aggregator was committed as a clean evidence pass and consumed
    the weekly cadence.
    """
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    fake_bin = _build_isolated_bin(
        tmp_path, agg_body="#!/usr/bin/env python3\nimport sys\nsys.exit(7)\n"
    )

    pre_profile = (coach_dir / "profile.yaml").read_text()
    pre_changelog_size = (coach_dir / "changelog.md").stat().st_size
    marker = coach_dir / ".last_weekly_insights"
    assert not marker.exists()

    env = {
        **os.environ,
        "COACH_DIR_OVERRIDE": str(coach_dir),
        "COACH_FACETS_DIR": str(facets),
        "COACH_INSIGHTS_LLM_SKIP_REFRESH": "1",
        "GIT_DIR": "",
        "GIT_WORK_TREE": "",
    }
    result = subprocess.run(
        ["bash", str(fake_bin / "insights-llm.sh"), "--force"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, (
        f"wrapper exited 0 despite aggregator failure:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "bailing before merge" in result.stderr, result.stderr
    # Throttle marker MUST NOT exist — the next session start should
    # retry rather than wait 7 more days on a failed run.
    assert not marker.exists(), "throttle marker was touched despite aggregator failure"
    # Profile + changelog UNCHANGED.
    assert (coach_dir / "profile.yaml").read_text() == pre_profile, (
        "profile.yaml was mutated despite aggregator failure"
    )
    assert (coach_dir / "changelog.md").stat().st_size == pre_changelog_size, (
        "changelog.md was appended to despite aggregator failure"
    )


def test_aggregator_garbled_output_bails_before_merge(tmp_path: Path) -> None:
    """An aggregator that exits 0 but emits unparseable JSON must also
    bail before merge — merging an unreadable detections file as `[]`
    is the same failure mode as a nonzero aggregator exit, just one
    layer down."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    fake_bin = _build_isolated_bin(
        tmp_path,
        agg_body=(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write('not valid json {{{')\n"
            "sys.exit(0)\n"
        ),
    )

    pre_profile = (coach_dir / "profile.yaml").read_text()
    marker = coach_dir / ".last_weekly_insights"

    env = {
        **os.environ,
        "COACH_DIR_OVERRIDE": str(coach_dir),
        "COACH_FACETS_DIR": str(facets),
        "COACH_INSIGHTS_LLM_SKIP_REFRESH": "1",
        "GIT_DIR": "",
        "GIT_WORK_TREE": "",
    }
    result = subprocess.run(
        ["bash", str(fake_bin / "insights-llm.sh"), "--force"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unparseable" in result.stderr or "bailing" in result.stderr
    assert not marker.exists()
    assert (coach_dir / "profile.yaml").read_text() == pre_profile


def test_concurrent_run_skips_when_lock_held(tmp_path: Path) -> None:
    """If another process already holds .weekly_insights.lock, the
    wrapper must exit 10 (skipped) without running the LLM call,
    aggregator, or merge. Guards the P1 race where two SessionStart
    hooks fire within the slow `claude -p /insights` window —
    without this serialization, both wrappers would run the LLM call,
    both aggregate, both merge, prematurely advancing
    debounce/graduation streaks."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)

    lock_path = coach_dir / ".weekly_insights.lock"
    lock_path.touch()
    fd = os.open(str(lock_path), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        result = _run(coach_dir, facets, "--force")
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    assert result.returncode == 10, (
        f"expected exit 10 (lock contention skip), got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "concurrent" in result.stdout.lower()
    # No merge ran — recent_runs untouched, no marker.
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    assert profile.get("recent_runs") in (None, [])
    assert profile.get("entries") in (None, [])
    assert not (coach_dir / ".last_weekly_insights").exists()


def test_concurrent_wrappers_only_one_merges(tmp_path: Path) -> None:
    """End-to-end concurrent run: launch two wrappers in parallel
    against a fixture with a slow (2s) aggregator. Exactly one must
    win the lock and merge; the other must exit 10. Profile gets
    one entry, recent_runs gets one append."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    slow_agg = (
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "sys.stdout.write(json.dumps([{\n"
        "  'id': 'misunderstood-request',\n"
        "  'name': 'misunderstood request',\n"
        "  'direction': 'negative',\n"
        "  'nudge': 'test',\n"
        "  'examples': [],\n"
        "  'priority': 2,\n"
        "}]))\n"
        "sys.stdout.flush()\n"
        "time.sleep(1.5)\n"
    )
    fake_bin = _build_isolated_bin(tmp_path, agg_body=slow_agg)

    env = {
        **os.environ,
        "COACH_DIR_OVERRIDE": str(coach_dir),
        "COACH_FACETS_DIR": str(facets),
        "COACH_INSIGHTS_LLM_SKIP_REFRESH": "1",
        "GIT_DIR": "",
        "GIT_WORK_TREE": "",
    }
    p_a = subprocess.Popen(
        ["bash", str(fake_bin / "insights-llm.sh"), "--force"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(0.2)
    p_b = subprocess.Popen(
        ["bash", str(fake_bin / "insights-llm.sh"), "--force"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out_a, err_a = p_a.communicate(timeout=15)
    out_b, err_b = p_b.communicate(timeout=15)

    rcs = sorted([p_a.returncode, p_b.returncode])
    assert rcs == [0, 10], (
        f"expected one winner (rc=0) + one skipper (rc=10), got {rcs}\n"
        f"A: rc={p_a.returncode} stdout={out_a!r} stderr={err_a!r}\n"
        f"B: rc={p_b.returncode} stdout={out_b!r} stderr={err_b!r}"
    )
    profile = yaml.safe_load((coach_dir / "profile.yaml").read_text())
    assert len(profile.get("recent_runs") or []) == 1, (
        f"expected exactly one recent_run after concurrent race, got {profile.get('recent_runs')}"
    )
    assert len(profile.get("entries") or []) == 1


# --- LLM-step fail-hard regression suite ----------------------------------
#
# Mirrors test_aggregator_failure_bails_before_merge. The bug class is the
# same — a refresh step that fails silently lets merge.py treat
# stale-or-empty facets as a clean evidence pass, advancing absence-based
# streaks on phantom data. The only difference is which step fails: these
# three cases cover the LLM refresh (insights-llm.sh:133–164) instead of
# the aggregator (insights-llm.sh:175–199).


def _aggregator_should_not_run_body() -> str:
    """Aggregator body that fails the test loudly if invoked.

    Used by the LLM-fail-hard tests below: when the wrapper exits 6
    *before* the aggregator stage (the desired behavior), this body is
    never executed. If the wrapper regresses to fail-soft and falls
    through, the aggregator will run and the test will catch it via a
    distinctive sentinel string in stderr.
    """
    return (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('AGG_RAN_BUT_SHOULD_NOT_HAVE\\n')\n"
        "sys.stdout.write('[]\\n')\n"
        "sys.exit(0)\n"
    )


def test_missing_claude_bails_before_merge(tmp_path: Path) -> None:
    """When `claude` is absent from PATH the wrapper MUST exit 6 before
    aggregating, merging, or touching the throttle marker. Reproduces
    the v0.5.1 P1 #1a teammate finding: a 4/5 weakness graduated with
    +5 XP under fail-soft + missing claude + empty facets."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    fake_bin = _build_isolated_bin(
        tmp_path, agg_body=_aggregator_should_not_run_body()
    )

    pre_profile = (coach_dir / "profile.yaml").read_text()
    marker = coach_dir / ".last_weekly_insights"
    assert not marker.exists()

    # Sandbox PATH has python3 + bash but NO claude.
    path = _sandbox_path_dir(tmp_path)

    result = _run_with_path(
        coach_dir=coach_dir, facets=facets, fake_bin=fake_bin, path=path
    )

    assert result.returncode == 6, (
        f"expected exit 6 (LLM refresh failed), got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "claude CLI not on PATH" in result.stderr
    assert "bailing before merge" in result.stderr
    assert "AGG_RAN_BUT_SHOULD_NOT_HAVE" not in result.stderr, (
        "wrapper fell through to aggregator despite missing claude"
    )
    assert not marker.exists(), "throttle marker was touched despite missing claude"
    assert (coach_dir / "profile.yaml").read_text() == pre_profile


def test_claude_nonzero_exit_bails_before_merge(tmp_path: Path) -> None:
    """When `claude -p /insights` exits nonzero (e.g. plan does not
    grant access, transient API failure), the wrapper MUST exit 6
    before merge/marker — same reasoning as missing-claude."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    fake_bin = _build_isolated_bin(
        tmp_path,
        agg_body=_aggregator_should_not_run_body(),
        claude_body="#!/bin/sh\nexit 1\n",
    )

    pre_profile = (coach_dir / "profile.yaml").read_text()
    marker = coach_dir / ".last_weekly_insights"

    path = _sandbox_path_dir(tmp_path, extra_dir=fake_bin)

    result = _run_with_path(
        coach_dir=coach_dir, facets=facets, fake_bin=fake_bin, path=path
    )

    assert result.returncode == 6, (
        f"expected exit 6, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "exited rc=" in result.stderr
    assert "bailing before merge" in result.stderr
    assert "AGG_RAN_BUT_SHOULD_NOT_HAVE" not in result.stderr
    assert not marker.exists()
    assert (coach_dir / "profile.yaml").read_text() == pre_profile


def test_no_evidence_bails_before_merge(tmp_path: Path) -> None:
    """When the aggregator finds n_sessions == 0 in the window it exits
    EXIT_NO_EVIDENCE=3; the wrapper MUST translate to its own exit 7 and
    bail before merge/marker. Reproduces v0.5.1 P1 #1b: a successful
    `claude -p` that writes zero current-window facets used to merge
    `detections=[]` as a clean evidence pass.

    Skips the LLM step (COACH_INSIGHTS_LLM_SKIP_REFRESH=1) so the
    aggregator runs against the seeded empty facets dir directly."""
    coach_dir = _seed_coach_dir(tmp_path)
    empty_facets = tmp_path / "empty-facets"
    empty_facets.mkdir()
    pre_profile = (coach_dir / "profile.yaml").read_text()
    pre_changelog_size = (coach_dir / "changelog.md").stat().st_size
    marker = coach_dir / ".last_weekly_insights"
    assert not marker.exists()

    result = _run(coach_dir, empty_facets, "--force")

    assert result.returncode == 7, (
        f"expected exit 7 (no evidence), got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "no current-window evidence" in result.stderr
    assert "bailing before merge" in result.stderr
    assert not marker.exists(), "throttle marker was touched despite no evidence"
    assert (coach_dir / "profile.yaml").read_text() == pre_profile
    assert (coach_dir / "changelog.md").stat().st_size == pre_changelog_size


def test_claude_timeout_bails_before_merge(tmp_path: Path) -> None:
    """When `claude -p /insights` exceeds COACH_INSIGHTS_LLM_TIMEOUT,
    the wrapper kills the subprocess and MUST exit 6 — not fall through
    to aggregation. NOTE: timeout is set via the env var, not a CLI
    flag (the wrapper exits 2 with 'unknown arg' if you pass --timeout)."""
    coach_dir = _seed_coach_dir(tmp_path)
    facets = _seed_facets(tmp_path)
    # Sleep well past the test's timeout. The wrapper polls every 2s
    # so a 4s timeout means the kill fires at the 4s tick.
    fake_bin = _build_isolated_bin(
        tmp_path,
        agg_body=_aggregator_should_not_run_body(),
        claude_body="#!/bin/sh\nsleep 60\n",
    )

    pre_profile = (coach_dir / "profile.yaml").read_text()
    marker = coach_dir / ".last_weekly_insights"

    path = _sandbox_path_dir(tmp_path, extra_dir=fake_bin)

    result = _run_with_path(
        coach_dir=coach_dir,
        facets=facets,
        fake_bin=fake_bin,
        path=path,
        extra_env={"COACH_INSIGHTS_LLM_TIMEOUT": "4"},
        timeout=30,
    )

    assert result.returncode == 6, (
        f"expected exit 6 (timeout), got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "timed out" in result.stderr
    assert "bailing before merge" in result.stderr
    assert "AGG_RAN_BUT_SHOULD_NOT_HAVE" not in result.stderr
    assert not marker.exists()
    assert (coach_dir / "profile.yaml").read_text() == pre_profile


# --- merge.py marker path isolation (v0.5.1 P1 #2) ------------------------
# Pre-v0.5.1, merge.py hardcoded marker paths under
# Path.home() / ".claude/coach/", so a sandboxed run with
# COACH_DIR_OVERRIDE leaked .pending_* markers into the live install.
# Fix: main() reassigns the module globals to args.profile.parent
# before calling merge(). This test exercises the CLI path end-to-end
# and verifies live-install markers are byte-identical pre/post.

import hashlib  # noqa: E402

MERGE_PY = BIN_DIR / "merge.py"
LIVE_COACH_DIR = Path.home() / ".claude" / "coach"
LIVE_MARKERS = (
    LIVE_COACH_DIR / ".pending_graduation",
    LIVE_COACH_DIR / ".pending_streak_rewards",
    LIVE_COACH_DIR / ".pending_regression",
)


def _snapshot_marker(p: Path) -> tuple:
    """Return (mtime_ns, sha256) for a marker file, or (None, None) if
    it doesn't exist. mtime_ns + content hash is strictly stronger than
    `exists()`: a write that produces identical bytes at the same-second
    mtime would still bump mtime_ns, and any byte change is hashed."""
    if not p.exists():
        return (None, None)
    st = p.stat()
    return (st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())


def test_merge_writes_markers_under_profile_parent(tmp_path: Path) -> None:
    """merge.py CLI with --profile <tmp>/profile.yaml MUST write the
    three .pending_* markers under <tmp>/ — never to the live install
    under ~/.claude/coach/. Snapshots live-install markers (mtime_ns +
    sha256) pre/post and asserts byte-identical preservation."""
    pre_live = {p: _snapshot_marker(p) for p in LIVE_MARKERS}

    coach_dir = tmp_path / "coach"
    coach_dir.mkdir()

    # Seed a profile with one entry at clean_streak_runs=4. The next
    # empty-detections merge ticks it to 5 → graduation → marker write.
    profile_yaml = coach_dir / "profile.yaml"
    profile_yaml.write_text(yaml.safe_dump({
        "schema_version": 1,
        "updated": None,
        "entries": [{
            "id": "test-weakness", "name": "test weakness",
            "tier": "active", "direction": "negative",
            "confidence": 0.8, "priority": 3,
            "nudge": "stop doing that", "examples": [],
            "first_seen": "2026-03-01",
            "last_seen_in_run": "2026-04-01",
            "clean_streak_runs": 4, "positive_run_streak": 0,
            "source_runs": ["old"], "total_occurrences": 10,
        }],
        "recent_runs": ["r-a", "r-b", "r-c"],
    }))
    (coach_dir / "changelog.md").touch()
    detections_json = tmp_path / "detections.json"
    detections_json.write_text("[]")

    result = subprocess.run(
        [
            "python3", str(MERGE_PY),
            "--profile", str(profile_yaml),
            "--changelog", str(coach_dir / "changelog.md"),
            "--lock", str(coach_dir / ".lock"),
            "--detections", str(detections_json),
            "--run-id", "insights-weekly-marker-isolation-test",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"merge.py failed: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    # Markers MUST land under the tmp coach dir (the entry graduated,
    # so .pending_graduation must exist; others may be empty/absent).
    tmp_grad = coach_dir / ".pending_graduation"
    assert tmp_grad.exists(), (
        f"expected .pending_graduation under tmp coach dir; "
        f"contents={list(coach_dir.iterdir())}"
    )
    grad_payload = json.loads(tmp_grad.read_text())
    grad_ids = [g.get("id") for g in grad_payload.get("graduations", [])]
    assert "test-weakness" in grad_ids, (
        f"graduation marker missing test-weakness: {grad_payload}"
    )

    # Live-install markers MUST be byte-identical to the pre-snapshot.
    post_live = {p: _snapshot_marker(p) for p in LIVE_MARKERS}
    for p in LIVE_MARKERS:
        assert pre_live[p] == post_live[p], (
            f"live-install marker mutated by sandboxed merge: {p}\n"
            f"pre={pre_live[p]} post={post_live[p]}"
        )
