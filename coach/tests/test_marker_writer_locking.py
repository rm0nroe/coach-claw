"""Marker writer locking — guards against the v0.2.0 race where readers
acquired a sidecar flock but writers in `merge.py` / `stats.py` did not.

Race the test reproduces:
  1. Marker exists with [old], consumed_by=[].
  2. Reader (no lock yet) reads → snapshot of [old].
  3. /coach-insights writer commits new → marker becomes [old, new].
  4. Reader replaces with stale snapshot → marker drops back to [old].
  5. The "new" event is silently lost.

Fix under test: writers acquire the same `<marker>.lock` flock the reader
takes in `_read_and_consume()`, so the read-modify-write windows are
serialized and a stale-snapshot reader can't clobber a writer's commit.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def merge_mod(tmp_path, monkeypatch):
    """Load coach/bin/merge.py with marker paths redirected to tmp_path."""
    path = Path(__file__).resolve().parents[1] / "bin" / "merge.py"
    spec = importlib.util.spec_from_file_location(
        f"merge_locking_{tmp_path.name}", str(path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "GRADUATION_MARKER", tmp_path / ".pending_graduation")
    monkeypatch.setattr(mod, "REGRESSION_MARKER", tmp_path / ".pending_regression")
    monkeypatch.setattr(mod, "STREAK_REWARD_MARKER", tmp_path / ".pending_streak_rewards")
    return mod


@pytest.fixture
def stats_mod(tmp_path, monkeypatch):
    """Load coach/bin/stats.py with marker paths redirected to tmp_path."""
    path = Path(__file__).resolve().parents[1] / "bin" / "stats.py"
    spec = importlib.util.spec_from_file_location(
        f"stats_locking_{tmp_path.name}", str(path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LEVELUP_MARKER", tmp_path / ".pending_levelup")
    monkeypatch.setattr(mod, "LEVEL_STATE", tmp_path / ".level_state.json")
    return mod


@pytest.fixture
def cup_mod(tmp_path, monkeypatch):
    """Load hooks/coach-user-prompt.py with tip state redirected to tmp_path."""
    path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    spec = importlib.util.spec_from_file_location(
        f"cup_locking_{tmp_path.name}", str(path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TIP_STATE", tmp_path / ".tip_state.json")
    return mod


def _hold_lock(lock_path: Path):
    """Open and exclusively lock a sidecar lockfile. Returns the file
    handle so the caller can release it explicitly."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def test_graduation_writer_blocks_when_reader_lock_held(merge_mod, tmp_path):
    """The graduation marker writer must wait on the same sidecar flock
    the reader uses. Without writer locking this thread would complete
    immediately and a stale-snapshot reader could clobber its write."""
    marker = tmp_path / ".pending_graduation"
    lock_path = tmp_path / ".pending_graduation.lock"
    holder = _hold_lock(lock_path)
    completed = threading.Event()

    def writer():
        merge_mod._append_graduation_marker(
            [{"id": "new", "name": "New"}],
            datetime.now(timezone.utc),
        )
        completed.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    # Writer must NOT complete while the lock is held.
    assert not completed.wait(timeout=0.3), (
        "writer should be blocked on the marker sidecar lock"
    )

    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()

    assert completed.wait(timeout=2.0), (
        "writer should complete promptly after lock is released"
    )
    t.join(timeout=1.0)

    raw = json.loads(marker.read_text())
    assert raw["graduations"] == [{"id": "new", "name": "New"}]
    assert raw["consumed_by"] == []  # fresh write resets


def test_regression_writer_takes_lock(merge_mod, tmp_path):
    marker = tmp_path / ".pending_regression"
    lock_path = tmp_path / ".pending_regression.lock"
    holder = _hold_lock(lock_path)
    completed = threading.Event()

    def writer():
        merge_mod._append_regression_marker(
            [{"id": "r1", "name": "R1"}],
            datetime.now(timezone.utc),
        )
        completed.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert not completed.wait(timeout=0.3)
    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()
    assert completed.wait(timeout=2.0)
    t.join(timeout=1.0)
    assert json.loads(marker.read_text())["regressions"] == [{"id": "r1", "name": "R1"}]


def test_streak_reward_writer_takes_lock(merge_mod, tmp_path):
    marker = tmp_path / ".pending_streak_rewards"
    lock_path = tmp_path / ".pending_streak_rewards.lock"
    holder = _hold_lock(lock_path)
    completed = threading.Event()

    def writer():
        merge_mod._append_streak_reward_marker(
            [{"id": "s1", "name": "S1", "streak": 2}],
            datetime.now(timezone.utc),
        )
        completed.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert not completed.wait(timeout=0.3)
    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()
    assert completed.wait(timeout=2.0)
    t.join(timeout=1.0)
    assert json.loads(marker.read_text())["rewards"] == [
        {"id": "s1", "name": "S1", "streak": 2}
    ]


def test_levelup_writer_takes_lock(stats_mod, tmp_path):
    """`stats.py:_check_and_mark_level_up` must lock around its level-up
    marker write so a stale-snapshot reader can't drop a fresh celebration."""
    marker = tmp_path / ".pending_levelup"
    lock_path = tmp_path / ".pending_levelup.lock"
    holder = _hold_lock(lock_path)
    completed = threading.Event()

    def writer():
        # First-ever level-up path: last_state is None, current_idx > 0.
        stats_mod._check_levelup(
            current_idx=2,
            current_name=stats_mod.LEVELS[2][1],
            xp=120,
        )
        completed.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert not completed.wait(timeout=0.3), (
        "level-up writer should block on marker sidecar lock"
    )

    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()

    assert completed.wait(timeout=2.0)
    t.join(timeout=1.0)

    raw = json.loads(marker.read_text())
    assert raw["to_idx"] == 2
    assert raw["xp_at_levelup"] == 120
    assert raw["consumed_by"] == []


def test_level_state_writer_takes_lock(stats_mod, tmp_path):
    """The high-water level-state file must be serialized too.

    Without this lock, concurrent statusline renders could both observe a stale
    or missing .level_state.json and duplicate the same level-up marker.
    """
    lock_path = tmp_path / ".level_state.json.lock"
    holder = _hold_lock(lock_path)
    completed = threading.Event()

    def writer():
        stats_mod._check_levelup(
            current_idx=0,
            current_name=stats_mod.LEVELS[0][1],
            xp=0,
        )
        completed.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert not completed.wait(timeout=0.3), (
        "level-state writer should block on the level-state sidecar lock"
    )

    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()

    assert completed.wait(timeout=2.0)
    t.join(timeout=1.0)
    assert json.loads((tmp_path / ".level_state.json").read_text()) == {"level_idx": 0}


def test_tip_state_writer_takes_lock(cup_mod, tmp_path):
    lock_path = tmp_path / ".tip_state.json.lock"
    holder = _hold_lock(lock_path)
    completed = threading.Event()

    def writer():
        cup_mod._save_tip_state({"last_global_fire": "2026-05-06T00:00:00+00:00"})
        completed.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert not completed.wait(timeout=0.3), (
        "tip-state writer should block on the tip-state sidecar lock"
    )

    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()

    assert completed.wait(timeout=2.0)
    t.join(timeout=1.0)
    raw = json.loads((tmp_path / ".tip_state.json").read_text())
    assert raw["last_global_fire"] == "2026-05-06T00:00:00+00:00"


def test_existing_entries_preserved_across_writes(merge_mod, tmp_path):
    """Two sequential writer calls must merge into a combined list, not
    overwrite. consumed_by + created_at reset on each write so newly-added
    entries reach previously-consumed sessions."""
    now = datetime.now(timezone.utc)
    merge_mod._append_graduation_marker(
        [{"id": "old", "name": "Old"}], now
    )
    raw_before = json.loads((tmp_path / ".pending_graduation").read_text())
    assert [g["id"] for g in raw_before["graduations"]] == ["old"]

    # Pretend a session consumed it.
    raw_before["consumed_by"] = ["session-A"]
    (tmp_path / ".pending_graduation").write_text(json.dumps(raw_before))

    merge_mod._append_graduation_marker(
        [{"id": "new", "name": "New"}], now
    )
    raw_after = json.loads((tmp_path / ".pending_graduation").read_text())
    assert [g["id"] for g in raw_after["graduations"]] == ["old", "new"]
    assert raw_after["consumed_by"] == [], (
        "writer must reset consumed_by so already-consumed sessions still see "
        "the new entry on their next poll"
    )


def test_concurrent_writers_dont_lose_entries(merge_mod, tmp_path):
    """Stress test: 20 concurrent _append_graduation_marker calls must
    land all 20 entries (no race-window drops). With unlocked writers
    this would lose entries; with locking it must not."""
    now = datetime.now(timezone.utc)
    threads = []
    for i in range(20):
        ident = f"g{i}"
        t = threading.Thread(
            target=lambda i=ident: merge_mod._append_graduation_marker(
                [{"id": i, "name": i}], now
            ),
            daemon=True,
        )
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()

    raw = json.loads((tmp_path / ".pending_graduation").read_text())
    ids = sorted(g["id"] for g in raw["graduations"])
    expected = sorted(f"g{i}" for i in range(20))
    assert ids == expected, f"expected all 20 entries, got {ids}"
