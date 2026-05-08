"""Unit tests for coach/bin/aggregate_facets.py.

Mock facets/*.json sidecar fixtures, assert threshold-based emit shape
matches what merge.py expects on its --detections input.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import aggregate_facets


def _write_facet(dir_: Path, name: str, payload: dict) -> Path:
    p = dir_ / f"{name}.json"
    p.write_text(json.dumps(payload))
    return p


def _make_session(
    *,
    friction: dict | None = None,
    primary_success: str | None = None,
    friction_detail: str = "",
    brief_summary: str = "",
    session_id: str = "test-session",
) -> dict:
    out: dict = {"session_id": session_id}
    if friction is not None:
        out["friction_counts"] = friction
    if primary_success is not None:
        out["primary_success"] = primary_success
    if friction_detail:
        out["friction_detail"] = friction_detail
    if brief_summary:
        out["brief_summary"] = brief_summary
    return out


def test_friction_counts_emits_negative_detection(tmp_path: Path) -> None:
    """friction_counts.misunderstood_request in 4/5 sessions → emit negative."""
    for i in range(4):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(
                friction={"misunderstood_request": 2},
                friction_detail=f"session {i} got off-track on the first attempt",
                session_id=f"s{i}",
            ),
        )
    _write_facet(tmp_path, "s4", _make_session(session_id="s4"))

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    ids = [d["id"] for d in dets]
    assert "misunderstood-request" in ids
    det = next(d for d in dets if d["id"] == "misunderstood-request")
    assert det["direction"] == "negative"
    assert det["ratio"] == 0.8
    assert det["n_sessions"] == 5


def test_primary_success_emits_positive_detection(tmp_path: Path) -> None:
    """6/10 sessions with primary_success=good_debugging → emit positive."""
    for i in range(6):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(
                primary_success="good_debugging",
                brief_summary=f"session {i}: drove the bug to root cause",
                session_id=f"s{i}",
            ),
        )
    for i in range(6, 10):
        _write_facet(tmp_path, f"s{i}", _make_session(session_id=f"s{i}"))

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    ids = [d["id"] for d in dets]
    assert "good-debugging" in ids
    det = next(d for d in dets if d["id"] == "good-debugging")
    assert det["direction"] == "positive"
    assert det["ratio"] == 0.6


def test_below_threshold_drops_detection(tmp_path: Path) -> None:
    """Friction in 2/10 sessions (<25%) → not emitted."""
    for i in range(2):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(friction={"buggy_code": 1}, session_id=f"s{i}"),
        )
    for i in range(2, 10):
        _write_facet(tmp_path, f"s{i}", _make_session(session_id=f"s{i}"))

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert "buggy-code" not in [d["id"] for d in dets]


def test_strength_threshold_higher_than_negative(tmp_path: Path) -> None:
    """5/10 (50%) primary_success does NOT emit; needs ≥60%."""
    for i in range(5):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(primary_success="multi_file_changes", session_id=f"s{i}"),
        )
    for i in range(5, 10):
        _write_facet(tmp_path, f"s{i}", _make_session(session_id=f"s{i}"))

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert "multi-file-changes" not in [d["id"] for d in dets]


def test_strength_at_threshold_emits(tmp_path: Path) -> None:
    """Exactly 60% (6/10) primary_success → emits."""
    for i in range(6):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(primary_success="multi_file_changes", session_id=f"s{i}"),
        )
    for i in range(6, 10):
        _write_facet(tmp_path, f"s{i}", _make_session(session_id=f"s{i}"))

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert "multi-file-changes" in [d["id"] for d in dets]


def test_id_kebab_normalization(tmp_path: Path) -> None:
    """friction_counts underscore keys emit kebab-case ids."""
    for i in range(3):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(
                friction={"misunderstood_request": 1, "wrong_approach": 1},
                session_id=f"s{i}",
            ),
        )
    _write_facet(tmp_path, "s3", _make_session(session_id="s3"))

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    ids = {d["id"] for d in dets}
    # Both 3/4 = 75% > 25% → both should emit.
    assert "misunderstood-request" in ids
    assert "wrong-approach" in ids
    # No underscores in any id.
    for d in dets:
        assert "_" not in d["id"]


def test_examples_capped_and_redacted(tmp_path: Path) -> None:
    """5 friction_detail strings → capped at 3, each ≤120 chars; file paths
    redacted."""
    raw_examples = [
        "Edited /Users/foo/project/src/main.py and broke the build for an hour",
        "The settings.py change cascaded into a migration regression",
        "Wrong approach in /tmp/bar/src/handler.go before we caught it on PR",
        "Went down the wrong rabbit hole on test_runner.ts for 30 minutes",
        "Misread the spec — the README.md said the opposite of what I assumed",
    ]
    for i, detail in enumerate(raw_examples):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(
                friction={"misunderstood_request": 1},
                friction_detail=detail,
                session_id=f"s{i}",
            ),
        )

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    det = next(d for d in dets if d["id"] == "misunderstood-request")
    assert len(det["examples"]) == 3
    for ex in det["examples"]:
        assert len(ex) <= 120
        # File-path tokens redacted.
        assert "/Users/foo/" not in ex
        assert "/tmp/bar/" not in ex
        # File-extension tokens redacted.
        assert "settings.py" not in ex
        assert "handler.go" not in ex
        assert "test_runner.ts" not in ex
        assert "README.md" not in ex


def test_window_filtering(tmp_path: Path) -> None:
    """Facets older than the window are dropped."""
    import os
    import time as _time

    # Stale: mtime 14d ago.
    stale_payload = _make_session(friction={"misunderstood_request": 5}, session_id="stale")
    p_stale = _write_facet(tmp_path, "stale", stale_payload)
    stale_ts = _time.time() - 14 * 86400
    os.utime(p_stale, (stale_ts, stale_ts))

    # Fresh: today.
    for i in range(3):
        _write_facet(
            tmp_path,
            f"fresh{i}",
            _make_session(friction={"buggy_code": 1}, session_id=f"fresh{i}"),
        )

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    ids = [d["id"] for d in dets]
    # Stale not counted: only 3 sessions in window, all with buggy_code → emit it.
    assert "buggy-code" in ids
    # Stale's misunderstood_request should NOT emit.
    assert "misunderstood-request" not in ids
    det = next(d for d in dets if d["id"] == "buggy-code")
    assert det["n_sessions"] == 3


def test_missing_facets_dir_returns_empty(tmp_path: Path) -> None:
    """Nonexistent facets dir → empty list, no crash."""
    nonexistent = tmp_path / "nope"
    dets = aggregate_facets.aggregate(nonexistent, window_days=7, cap=8)
    assert dets == []


def test_empty_facets_dir_returns_empty(tmp_path: Path) -> None:
    """Existing but empty facets dir → empty list."""
    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert dets == []


def test_malformed_json_skipped(tmp_path: Path) -> None:
    """Malformed JSON files are skipped silently."""
    (tmp_path / "broken.json").write_text("not valid json {{{")
    for i in range(3):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(friction={"buggy_code": 1}, session_id=f"s{i}"),
        )

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert "buggy-code" in [d["id"] for d in dets]


def test_cap_enforced(tmp_path: Path) -> None:
    """cap=2 limits output to 2 detections, highest ratio first."""
    keys = ["misunderstood_request", "wrong_approach", "buggy_code", "edge_case"]
    # All four hit 100%.
    for i in range(5):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(
                friction={k: 1 for k in keys},
                session_id=f"s{i}",
            ),
        )

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=2)
    assert len(dets) == 2


def test_schema_shape_matches_merge_input(tmp_path: Path) -> None:
    """Detection objects must carry the fields merge.py reads."""
    for i in range(3):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(
                friction={"misunderstood_request": 1},
                friction_detail=f"detail {i}",
                session_id=f"s{i}",
            ),
        )

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert dets, "expected at least one detection"
    for d in dets:
        assert "id" in d and isinstance(d["id"], str) and d["id"]
        assert d["direction"] in ("positive", "negative")
        assert "name" in d
        assert "nudge" in d
        assert "examples" in d and isinstance(d["examples"], list)
        assert d.get("source") == "insights-weekly"


def test_zero_count_friction_not_emitted(tmp_path: Path) -> None:
    """friction_counts entries with count=0 are not treated as present."""
    for i in range(5):
        _write_facet(
            tmp_path,
            f"s{i}",
            _make_session(friction={"misunderstood_request": 0}, session_id=f"s{i}"),
        )

    dets = aggregate_facets.aggregate(tmp_path, window_days=7, cap=8)
    assert "misunderstood-request" not in [d["id"] for d in dets]


# --- CLI-level evidence gate (v0.5.1 P1 #1b) -------------------------------
# `aggregate()` continues to return [] for empty/missing dirs (those tests
# above stay green). The CLI `main()` adds an "evidence gate": if
# n_sessions == 0 in the requested window, exit 3 (EXIT_NO_EVIDENCE) and
# print no JSON to stdout. The wrapper translates that to its own exit 7.
# Reasoning: empty detections WITH n_sessions > 0 is valid (clean week,
# merges normally); empty detections WITH n_sessions == 0 is no evidence
# and must NOT advance absence-based streaks.

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "aggregate_facets.py"


def test_no_sessions_in_window_returns_3(tmp_path: Path) -> None:
    """Empty facets dir → CLI exits 3, prints no JSON to stdout, prints
    a clear stderr message naming the window. Pinned by
    aggregate_facets.EXIT_NO_EVIDENCE."""
    empty = tmp_path / "empty-facets"
    empty.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--facets-dir", str(empty), "--window-days", "7"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == aggregate_facets.EXIT_NO_EVIDENCE == 3, (
        f"expected exit 3, got {result.returncode}\nstderr={result.stderr}"
    )
    assert "no sessions in last 7 days" in result.stderr
    assert "refusing to emit detections" in result.stderr
    # Stdout MUST be empty so a caller piping stdout into merge gets a
    # parse error instead of a silent `[]` merge.
    assert result.stdout.strip() == "", (
        f"stdout should be empty when bailing on no-evidence: {result.stdout!r}"
    )


def test_nonexistent_facets_dir_cli_returns_3(tmp_path: Path) -> None:
    """Same gate fires when --facets-dir doesn't exist (the function
    returns []; the CLI catches it via the n_sessions recount)."""
    nonexistent = tmp_path / "does-not-exist"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--facets-dir", str(nonexistent)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    assert "no sessions" in result.stderr
    assert result.stdout.strip() == ""


def test_session_with_no_detections_still_exits_0(tmp_path: Path) -> None:
    """The gate fires on n_sessions==0, NOT on detections==0. A clean
    session (no friction, no primary_success) with n_sessions=1 emits
    no detections but is a legitimate clean signal and must merge as
    `[]` — exit 0, NOT exit 3. This pins the asymmetry from
    `_session_with_no_detections_still_exits_0` vs the no-evidence
    gate."""
    _write_facet(tmp_path, "s0", _make_session(session_id="s0"))

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--facets-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"single-session-no-friction should exit 0, got {result.returncode}\n"
        f"stderr={result.stderr}"
    )
    assert "n_sessions=1" in result.stderr
    assert "detections=0" in result.stderr
    # Empty detections list, NOT empty stdout — wrapper merges this as
    # a clean week.
    assert json.loads(result.stdout) == []
