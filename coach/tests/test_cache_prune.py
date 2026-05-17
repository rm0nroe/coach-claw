"""cache_prune.py: prune old plugin cache dirs.

Claude Code's `/plugin update` never garbage-collects prior versions
under ~/.claude/plugins/cache/<marketplace>/<plugin>/. cache_prune walks
the cache root, identifies the active version from
installed_plugins.json, and removes strictly-older sibling dirs. Race
guard skips dirs touched within RECENT_MTIME_THRESHOLD_SECONDS.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import cache_prune


@pytest.fixture
def cache_root(tmp_path):
    """Synthesize a per-plugin cache root with version subdirs."""
    root = tmp_path / "cache"
    root.mkdir()
    return root


@pytest.fixture
def installed_plugins(tmp_path):
    """Synthesize an installed_plugins.json with one coach-claw entry."""
    p = tmp_path / "installed_plugins.json"

    def _write(version: str, scope: str = "user") -> Path:
        data = {
            "version": 2,
            "plugins": {
                "coach-claw@coach-claw-plugins": [
                    {
                        "scope": scope,
                        "version": version,
                        "installPath": f"/fake/path/{version}",
                        "gitCommitSha": "deadbeef",
                    }
                ]
            },
        }
        p.write_text(json.dumps(data))
        return p

    return _write


def _mkversion(cache_root: Path, version: str, *, age_seconds: int = 3600) -> Path:
    """Create a version subdir with mtime aged to past."""
    d = cache_root / version
    d.mkdir()
    (d / "stub").write_text("")  # contents don't matter
    mtime = time.time() - age_seconds
    import os
    os.utime(d, (mtime, mtime))
    return d


def test_active_preserved_older_removed(cache_root, installed_plugins):
    """Active + N-3 predecessor window kept; everything older pruned."""
    _mkversion(cache_root, "0.1.5")
    _mkversion(cache_root, "0.1.6")
    _mkversion(cache_root, "0.1.13")
    _mkversion(cache_root, "0.1.14")
    active_dir = _mkversion(cache_root, "0.1.15")
    plugins_path = installed_plugins("0.1.15")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert active_dir.exists()
    # v0.1.22+: keep the 3 newest predecessors as rollback buffer
    # (0.1.14, 0.1.13, 0.1.6). Older (0.1.5) gets pruned.
    assert (cache_root / "0.1.14").exists(), "N-1 must be retained"
    assert (cache_root / "0.1.13").exists(), "N-2 must be retained"
    assert (cache_root / "0.1.6").exists(), "N-3 must be retained"
    assert not (cache_root / "0.1.5").exists()
    assert [p.name for p in removed] == ["0.1.5"]


def test_predecessor_n_minus_3_retained(cache_root, installed_plugins):
    """v0.1.22+: 3 newest predecessors by semver kept; older versions pruned.

    Rationale: long-running CC processes bind plugin hook paths in memory
    at session start. Auto-prune wiping the dir those processes still
    reference triggers ENOENT on /clear, SessionStart, and UserPromptSubmit
    events. Keeping 3 predecessors absorbs multi-version-bump release trains
    (e.g. 0.1.17→0.1.21 across one commit) where a single N-1 window can
    drop the version a long-running session still references."""
    _mkversion(cache_root, "0.1.10")
    _mkversion(cache_root, "0.1.17")  # N-3
    _mkversion(cache_root, "0.1.18")  # N-2
    _mkversion(cache_root, "0.1.19")  # N-1
    active = _mkversion(cache_root, "0.1.20")
    plugins_path = installed_plugins("0.1.20")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert active.exists()
    assert (cache_root / "0.1.19").exists(), "N-1 must be retained"
    assert (cache_root / "0.1.18").exists(), "N-2 must be retained"
    assert (cache_root / "0.1.17").exists(), "N-3 must be retained"
    assert not (cache_root / "0.1.10").exists()
    assert [p.name for p in removed] == ["0.1.10"]


def test_multi_bump_release_train_keeps_session_version(cache_root, installed_plugins):
    """Regression for the v0.1.21 bug: a 5-version release train
    (0.1.17→0.1.21) used to drop the originating session's version
    out of an N-1 buffer after a single prune cycle, breaking running
    CC sessions whose CLAUDE_PLUGIN_ROOT was resolved at 0.1.19. Under
    N-3 retention, the originating version survives one bump cycle."""
    _mkversion(cache_root, "0.1.19")  # version a still-running session holds
    _mkversion(cache_root, "0.1.20")
    active = _mkversion(cache_root, "0.1.21")
    plugins_path = installed_plugins("0.1.21")

    cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert active.exists()
    assert (cache_root / "0.1.20").exists()
    assert (cache_root / "0.1.19").exists(), "originating session's version must survive"


def test_predecessor_promoted_on_next_prune(cache_root, installed_plugins):
    """After active bumps far enough, the OLDEST predecessor falls out
    of the N-3 window and gets pruned. Verifies the buffer doesn't
    accumulate indefinitely across releases."""
    _mkversion(cache_root, "0.1.17")  # falls out of N-3 (becomes N-4)
    _mkversion(cache_root, "0.1.18")  # N-3
    _mkversion(cache_root, "0.1.19")  # N-2
    _mkversion(cache_root, "0.1.20")  # N-1
    active = _mkversion(cache_root, "0.1.21")
    plugins_path = installed_plugins("0.1.21")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert active.exists()
    assert (cache_root / "0.1.20").exists()
    assert (cache_root / "0.1.19").exists()
    assert (cache_root / "0.1.18").exists()
    assert not (cache_root / "0.1.17").exists(), "N-4 must be pruned"
    assert [p.name for p in removed] == ["0.1.17"]


def test_recent_mtime_skipped(cache_root, installed_plugins):
    """Dirs touched within threshold left alone (active install race guard)."""
    fresh_dir = _mkversion(cache_root, "0.1.14", age_seconds=5)  # 5s ago
    _mkversion(cache_root, "0.1.15")
    plugins_path = installed_plugins("0.1.15")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert fresh_dir.exists()  # NOT removed despite being older
    assert removed == []


def test_newer_dir_never_removed(cache_root, installed_plugins):
    """Defensive: a dir newer than the active install is left alone."""
    _mkversion(cache_root, "0.1.15")
    newer = _mkversion(cache_root, "0.2.0")
    plugins_path = installed_plugins("0.1.15")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert newer.exists()
    assert removed == []


def test_non_semver_dir_ignored(cache_root, installed_plugins):
    """Random dir names (e.g. 'tmp-install') not parsed as versions."""
    (cache_root / "random-junk").mkdir()
    _mkversion(cache_root, "0.1.15")
    plugins_path = installed_plugins("0.1.15")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=plugins_path
    )

    assert (cache_root / "random-junk").exists()
    assert removed == []


def test_dry_run_no_op(cache_root, installed_plugins):
    """--dry-run returns the would-remove list but leaves disk untouched.
    Needs enough versions so the N-3 predecessor window isn't the only
    prunable — provide one version below the window."""
    oldest = _mkversion(cache_root, "0.1.5")
    _mkversion(cache_root, "0.1.6")   # N-3 — kept
    _mkversion(cache_root, "0.1.13")  # N-2 — kept
    _mkversion(cache_root, "0.1.14")  # N-1 — kept
    _mkversion(cache_root, "0.1.15")  # active
    plugins_path = installed_plugins("0.1.15")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root,
        installed_plugins_path=plugins_path,
        dry_run=True,
    )

    assert oldest.exists()
    assert (cache_root / "0.1.14").exists()  # dry-run + N-3 window both keep
    assert (cache_root / "0.1.13").exists()
    assert (cache_root / "0.1.6").exists()
    assert [p.name for p in removed] == ["0.1.5"]


def test_failsafe_returns_empty_on_missing_json(cache_root, tmp_path):
    """Malformed/missing installed_plugins.json → no-op."""
    missing = tmp_path / "no-such.json"
    _mkversion(cache_root, "0.1.6")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=missing
    )

    assert (cache_root / "0.1.6").exists()
    assert removed == []


def test_failsafe_returns_empty_on_garbled_json(cache_root, tmp_path):
    """Garbled JSON → no-op, never raises."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    _mkversion(cache_root, "0.1.6")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=bad
    )

    assert removed == []


def test_failsafe_returns_empty_when_no_entry(cache_root, tmp_path):
    """installed_plugins.json present but no coach-claw entry → no-op."""
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({"version": 2, "plugins": {}}))
    _mkversion(cache_root, "0.1.6")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=p
    )

    assert (cache_root / "0.1.6").exists()
    assert removed == []


def test_multiple_scope_entries_use_highest_version(cache_root, tmp_path):
    """When project + user scope both present, prune to the higher version."""
    p = tmp_path / "installed_plugins.json"
    data = {
        "version": 2,
        "plugins": {
            "coach-claw@coach-claw-plugins": [
                {"scope": "project", "version": "0.1.6"},
                {"scope": "user", "version": "0.1.15"},
            ]
        },
    }
    p.write_text(json.dumps(data))
    _mkversion(cache_root, "0.1.5")
    _mkversion(cache_root, "0.1.6")   # N-3 — kept under v0.1.22 rules
    _mkversion(cache_root, "0.1.13")  # N-2 — kept
    _mkversion(cache_root, "0.1.14")  # N-1 — kept
    active_dir = _mkversion(cache_root, "0.1.15")

    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=cache_root, installed_plugins_path=p
    )

    assert active_dir.exists()
    assert (cache_root / "0.1.14").exists()
    assert (cache_root / "0.1.13").exists()
    assert (cache_root / "0.1.6").exists()
    assert [p.name for p in removed] == ["0.1.5"]


def test_find_active_version_returns_highest():
    """find_active_version picks the highest semver across scope entries."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "version": 2,
            "plugins": {
                "coach-claw@coach-claw-plugins": [
                    {"version": "0.1.6"},
                    {"version": "0.1.15"},
                    {"version": "0.1.14"},
                ]
            },
        }, f)
        path = Path(f.name)
    try:
        assert cache_prune.find_active_version(path) == "0.1.15"
    finally:
        path.unlink()


def test_cache_root_missing_returns_empty(tmp_path, installed_plugins):
    """If cache root doesn't exist (fresh install case), no-op."""
    missing_root = tmp_path / "no-such-cache"
    plugins_path = installed_plugins("0.1.15")
    removed = cache_prune.prune_inactive_cache_versions(
        cache_root=missing_root, installed_plugins_path=plugins_path
    )
    assert removed == []
