"""coach-user-prompt.py: cron-nudge banner gating + install-summary wrap.

The plugin distribution emits a one-time `<coach-install-summary>`
block when (a) we're running under the plugin (CLAUDE_PLUGIN_ROOT set),
and (b) no Coach cron/launchd plist is registered. Guarded by the
`.cron-nudged` marker so it fires exactly once. The block wraps a
pre-rendered banner with explicit surface-verbatim instructions so the
model surfaces it at the top of its response instead of burying it as
ambient context (same fix pattern as `<coach-celebrate>` banners).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    """Load coach-user-prompt.py as a module via importlib."""
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_cron_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def coach_dir(tmp_path, monkeypatch):
    """Redirect COACH_DIR so the test doesn't touch the real
    `~/.claude/coach/.cron-nudged` marker."""
    d = tmp_path / "coach"
    d.mkdir()
    monkeypatch.setenv("COACH_CONFIG_DIR", str(d))
    return d


def test_no_nudge_when_not_in_plugin_context(cup, coach_dir, monkeypatch):
    """CLI distribution: CLAUDE_PLUGIN_ROOT unset → no nudge ever."""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    # Even with cron absent (mocked False), we still skip — gate is
    # CLAUDE_PLUGIN_ROOT, not cron presence.
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is None
    assert not (coach_dir / ".cron-nudged").exists()


def test_no_nudge_when_marker_present(cup, coach_dir, monkeypatch):
    """Marker present → already nudged → no re-nudge."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    (coach_dir / ".cron-nudged").write_text(json.dumps({"nudged_at": "2026-05-08T00:00:00Z"}))

    # Even if cron is absent we should NOT re-nudge.
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: False)

    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is None


def test_no_nudge_when_cron_already_registered(cup, coach_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: True)

    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is None
    # Marker NOT written (we didn't nudge, nothing to remember).
    assert not (coach_dir / ".cron-nudged").exists()


def test_nudge_fires_and_writes_marker(cup, coach_dir, monkeypatch):
    """Plugin context + cron absent + no prior marker → emit + write."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: False)

    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is not None
    # Banner mentions the recommended remediation
    assert "npx @rm0nroe/coach-claw launchd" in block
    assert "crontab" in block
    # Fires once: marker written
    marker = coach_dir / ".cron-nudged"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert "nudged_at" in payload


def test_nudge_is_idempotent_after_first_emit(cup, coach_dir, monkeypatch):
    """First call emits + writes marker; second call sees marker and
    returns None."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: False)

    first = cup._maybe_cron_nudge_block(env="terminal")
    second = cup._maybe_cron_nudge_block(env="terminal")
    assert first is not None
    assert second is None


def test_nudge_renders_ide_shape(cup, coach_dir, monkeypatch):
    """IDE entrypoint gets the HR-framed shape (consistent with other
    Coach blocks); terminal gets blockquote shape."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: False)

    block_ide = cup._cron_nudge_block(env="ide")
    block_term = cup._cron_nudge_block(env="terminal")
    assert block_ide.startswith("---")
    assert block_term.startswith(">")


def test_nudge_wrapped_in_install_summary_tag(cup, coach_dir, monkeypatch):
    """The maybe_-emit path wraps the banner in a <coach-install-summary>
    block with explicit "render verbatim at top" instructions — same
    surface-forcing pattern as <coach-celebrate>. Without the wrap, the
    model treats the banner as ambient context and buries it."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: False)

    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is not None
    # Open + close tags present
    assert "<coach-install-summary>" in block
    assert "</coach-install-summary>" in block
    # Load-bearing surface-verbatim instructions
    assert "VERBATIM" in block
    assert "very TOP of" in block
    assert "BEFORE any other content" in block
    # Banner is embedded inside the wrap
    assert "📅" in block
    assert "Daily insights need OS scheduling" in block
    assert "npx @rm0nroe/coach-claw launchd" in block


def test_nudge_banner_includes_plugin_version_and_path(cup, coach_dir, monkeypatch):
    """Banner copy beefed up to surface plugin version + install path,
    so the user (and any future debugger) can see exactly which plugin
    install the nudge fired from."""
    fake_root = "/Users/x/.claude/plugins/cache/coach-claw-plugins/coach-claw/9.9.9"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", fake_root)
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)
    import cron_check
    monkeypatch.setattr(cron_check, "is_cron_registered", lambda: False)

    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is not None
    assert "v9.9.9" in block
    assert fake_root in block


def test_failsafe_swallows_module_import_error(cup, coach_dir, monkeypatch):
    """If cron_check is somehow unavailable, return None — never raise
    out of a hook."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/some/plugin/root")
    monkeypatch.setattr(cup, "COACH_DIR", coach_dir)

    import sys
    monkeypatch.setitem(sys.modules, "cron_check", None)

    block = cup._maybe_cron_nudge_block(env="terminal")
    assert block is None
