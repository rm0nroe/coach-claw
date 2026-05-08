"""render_env.supports_dual_blade — glyph-capability probe.

Pins each branch of the detection order so a regression in one branch doesn't
mask a regression in another. Each test passes an explicit `env` mapping to
bypass the module-level cache (the cache is its own concern, tested separately
below).
"""
from __future__ import annotations

import pytest

import render_env
from render_env import supports_dual_blade


@pytest.fixture(autouse=True)
def _clear_dual_blade_cache():
    """Module-level cache must not leak between tests."""
    render_env._DUAL_BLADE_CACHE = None
    yield
    render_env._DUAL_BLADE_CACHE = None


def test_default_modern_terminal_returns_true():
    """Sensible default: a UTF-8 xterm with no overrides should render ⚔."""
    env = {"LANG": "en_US.UTF-8", "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is True


def test_force_ascii_kill_switch_wins():
    """COACH_FORCE_ASCII_GLYPHS truthy beats every other signal."""
    env = {
        "COACH_FORCE_ASCII_GLYPHS": "1",
        "COACH_SUPPORTS_DUAL_BLADE": "1",  # would otherwise be True
        "LANG": "en_US.UTF-8",
        "TERM": "xterm-256color",
    }
    assert supports_dual_blade(env) is False


def test_explicit_support_override_true():
    """COACH_SUPPORTS_DUAL_BLADE=1 forces True even if locale would say no."""
    env = {"COACH_SUPPORTS_DUAL_BLADE": "true", "LANG": "C", "TERM": "dumb"}
    assert supports_dual_blade(env) is True


def test_explicit_support_override_false():
    """COACH_SUPPORTS_DUAL_BLADE=0 forces False even on a UTF-8 xterm."""
    env = {"COACH_SUPPORTS_DUAL_BLADE": "0", "LANG": "en_US.UTF-8",
           "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is False


def test_non_utf8_locale_returns_false():
    """LANG=C with no UTF-8 anywhere → ASCII fallback."""
    env = {"LANG": "C", "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is False


def test_utf8_in_lc_all_alone_is_enough():
    """Locale check accepts LC_ALL=UTF-8 when LANG is unset."""
    env = {"LC_ALL": "en_US.UTF-8", "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is True


def test_lc_all_overrides_lang_per_posix():
    """POSIX precedence: LC_ALL wins over LANG. An explicit LC_ALL=C must
    NOT be overridden by an otherwise-unused LANG=en_US.UTF-8."""
    env = {"LC_ALL": "C", "LANG": "en_US.UTF-8", "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is False


def test_lc_ctype_overrides_lang_when_lc_all_empty():
    """When LC_ALL is unset, LC_CTYPE takes precedence over LANG."""
    env = {"LC_CTYPE": "C", "LANG": "en_US.UTF-8", "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is False


def test_lang_alone_is_consulted_when_others_empty():
    """LANG is the lowest-priority fallback — used only when LC_ALL and
    LC_CTYPE are both unset."""
    env = {"LANG": "en_US.UTF-8", "TERM": "xterm-256color"}
    assert supports_dual_blade(env) is True


def test_term_dumb_returns_false():
    """TERM=dumb is the canonical 'no fancy glyphs' signal."""
    env = {"LANG": "en_US.UTF-8", "TERM": "dumb"}
    assert supports_dual_blade(env) is False


def test_term_linux_returns_false():
    """Linux console framebuffer can't render U+2694."""
    env = {"LANG": "en_US.UTF-8", "TERM": "linux"}
    assert supports_dual_blade(env) is False


def test_empty_env_defaults_true():
    """No locale info at all → trust the default. Most cron / launchd
    invocations on modern macOS/Linux work fine without LANG set."""
    assert supports_dual_blade({}) is True


def test_module_cache_returns_first_probe_result():
    """First call (no env arg) populates the cache; subsequent calls
    return the cached value without re-probing."""
    render_env._DUAL_BLADE_CACHE = False
    assert supports_dual_blade() is False  # served from cache
    render_env._DUAL_BLADE_CACHE = True
    assert supports_dual_blade() is True


def test_explicit_env_does_not_pollute_cache():
    """Tests pass an explicit env to force a probe; that probe must not
    write back to the module cache (tests would interfere with each other
    and with the normal hot-path read)."""
    assert render_env._DUAL_BLADE_CACHE is None
    supports_dual_blade({"LANG": "C", "TERM": "dumb"})
    assert render_env._DUAL_BLADE_CACHE is None
