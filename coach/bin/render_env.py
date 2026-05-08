"""Render-environment detection for coach output.

Coach banners need different markdown shapes depending on whether they're
rendered by terminal Claude Code (which dims blockquotes via theme) or an
IDE chat panel (which uses a WebView markdown renderer with different
feature support — notably, no GFM admonitions and weak blockquote styling).

`detect_render_env()` returns "ide" or "terminal" based on Claude Code's
own `CLAUDE_CODE_ENTRYPOINT` env var. Allowlist semantics: known IDE
entrypoints get the IDE shape; everything else falls through to the
terminal shape, which renders correctly across all surfaces.

Single source of truth — imported by both hooks (which append coach/bin/
to sys.path before importing).
"""
from __future__ import annotations

import os
from typing import Literal, Mapping

# Entrypoints whose output is rendered by an IDE chat panel WebView.
#   vscode, jetbrains, ide-onboarding: confirmed in Claude Code binary
#   claude-vscode, claude-jetbrains:   speculative — kept as defensive
#     fallback for prefixed variants. If absent, detection falls through
#     to the terminal shape, which renders correctly in IDE panels too
#     (just less prominently than the HR-framed shape).
IDE_ENTRYPOINTS = frozenset({
    "vscode",
    "claude-vscode",
    "jetbrains",
    "claude-jetbrains",
    "ide-onboarding",
})

RenderEnv = Literal["ide", "terminal"]


def detect_render_env(env: Mapping[str, str] | None = None) -> RenderEnv:
    """Return "ide" if the hook is being invoked from an IDE chat panel,
    otherwise "terminal".

    Allowlist: unknown / future entrypoints default to "terminal" — the
    terminal shape uses universal markdown that renders acceptably
    everywhere, so it's the safe fallback when we don't recognize the
    surface.

    Honors COACH_RENDER_ENV={ide,terminal} as a manual override (useful
    for testing the IDE branch from a terminal session, or vice versa).

    Args:
        env: environment mapping. Defaults to os.environ. Explicit param
             so tests can pass a fake without monkeypatching.
    """
    if env is None:
        env = os.environ

    override = env.get("COACH_RENDER_ENV", "").strip().lower()
    if override in ("ide", "terminal"):
        return override  # type: ignore[return-value]

    entrypoint = env.get("CLAUDE_CODE_ENTRYPOINT", "").strip().lower()
    if entrypoint in IDE_ENTRYPOINTS:
        return "ide"
    return "terminal"


# -----------------------------------------------------------------------------
# Glyph-capability probes — lets bespoke banner themes ask "can this terminal
# render U+2694 ⚔ as a single cell?" before committing to it. Falls back to a
# 1-cell ASCII alternative (e.g., ✕) when the answer is no.
#
# Memoized at module level — probing the env is idempotent within a process,
# and every UserPromptSubmit hook spawns a fresh interpreter, so a per-process
# cache is enough. Tests pass an explicit `env` mapping to bypass the cache.

_DUAL_BLADE_CACHE: bool | None = None


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _is_falsy(value: str) -> bool:
    return value.strip().lower() in ("0", "false", "no", "off")


def supports_dual_blade(env: Mapping[str, str] | None = None) -> bool:
    """Return True if ⚔ (U+2694 CROSSED SWORDS) can be expected to render as
    a single cell in this terminal. Themes that build streak meters out of ⚔
    use this to fall back to ✕ when the renderer would mis-width the glyph.

    Detection order:
      1. COACH_FORCE_ASCII_GLYPHS truthy → False  (generic kill-switch — also
         applies to other dual-cell-risk glyphs added later).
      2. COACH_SUPPORTS_DUAL_BLADE set → honor explicitly (for tests + power
         users overriding a wrong default).
      3. Locale (LANG / LC_ALL / LC_CTYPE) lacks UTF-8 → False.
      4. TERM in {"dumb", "linux"} → False.
      5. Default → True. Modern terminals dominate; safer to render the
         intended glyph than to ASCII-degrade the majority.

    Args:
        env: environment mapping. Defaults to os.environ. When provided,
             the module-level cache is bypassed (so tests can pin shapes).
    """
    global _DUAL_BLADE_CACHE
    if env is None:
        if _DUAL_BLADE_CACHE is not None:
            return _DUAL_BLADE_CACHE
        env = os.environ
        cache = True
    else:
        cache = False

    result = _probe_dual_blade(env)
    if cache:
        _DUAL_BLADE_CACHE = result
    return result


def _probe_dual_blade(env: Mapping[str, str]) -> bool:
    if _is_truthy(env.get("COACH_FORCE_ASCII_GLYPHS", "")):
        return False

    explicit = env.get("COACH_SUPPORTS_DUAL_BLADE", "")
    if _is_truthy(explicit):
        return True
    if _is_falsy(explicit):
        return False

    # POSIX locale precedence: LC_ALL > LC_CTYPE > LANG. The first
    # non-empty value is the effective locale — OR-merging across all
    # three means an explicit `LC_ALL=C` is silently overridden by an
    # otherwise-unused `LANG=en_US.UTF-8`, which is the bug.
    effective = ""
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        val = env.get(var, "").strip()
        if val:
            effective = val.lower()
            break
    if effective and "utf-8" not in effective and "utf8" not in effective:
        return False

    term = env.get("TERM", "").strip().lower()
    if term in ("dumb", "linux"):
        return False

    return True
