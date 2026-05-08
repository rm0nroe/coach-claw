"""statusline_variants.py — every variant renders for sample inputs and
contains the expected key glyphs (level/name/elo/arrow)."""
from __future__ import annotations

import re

import statusline_variants as sv


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _sample_glyphs(level: int = 7, session_xp: int = 15) -> sv.Glyphs:
    return sv.Glyphs(
        level=level,
        name="Virtuoso",
        elo=1232,
        session_xp=session_xp,
        sigil_tier="silver",
        bar_pct=0.30,
    )


def test_every_variant_renders_a_non_empty_string():
    g = _sample_glyphs()
    for name in sv.VARIANTS:
        assert sv.render(name, g), f"{name} produced empty output"


def test_unknown_variant_falls_back_to_default():
    g = _sample_glyphs()
    fallback = sv.render("does-not-exist", g)
    assert fallback == sv.render(sv.DEFAULT_VARIANT, g)


def test_default_variant_is_crystal_and_includes_canonical_glyphs():
    """Pin v0.2.0 visual contract — `◆ Ⅶ 1232 Virtuoso ↑15` shape."""
    plain = _strip_ansi(sv.render("crystal", _sample_glyphs()))
    assert plain == "◆ Ⅶ 1232 Virtuoso ↑15"


def test_pips_variant_renders_pip_bar():
    plain = _strip_ansi(sv.render("pips", _sample_glyphs()))
    # bar_pct=0.30 → 3 filled pips, 7 empty
    assert plain.startswith("●●●○○○○○○○ Virtuoso")
    assert plain.endswith("↑15")


def test_bracket_variant_uses_brackets_and_contains_name_elo():
    plain = _strip_ansi(sv.render("bracket", _sample_glyphs()))
    assert plain == "[Ⅶ Virtuoso] 1232 ↑15"


def test_slash_variant_uses_path_separators():
    plain = _strip_ansi(sv.render("slash", _sample_glyphs()))
    assert plain == "L7 / Virtuoso / 1232 ↑15"


def test_forge_variant_uses_anvil_sigil():
    plain = _strip_ansi(sv.render("forge", _sample_glyphs()))
    assert plain == "⚒ Virtuoso · L7 · 1232 ↑15"


def test_zero_session_xp_drops_the_arrow():
    g = _sample_glyphs(session_xp=0)
    for name in sv.VARIANTS:
        plain = _strip_ansi(sv.render(name, g))
        assert "↑" not in plain, f"{name} kept the arrow at session_xp=0"


def test_to_roman_handles_full_50_level_range():
    expectations = {1: "Ⅰ", 4: "Ⅳ", 9: "Ⅸ", 10: "Ⅹ", 13: "ⅩⅢ", 50: "Ⅼ"}
    for n, want in expectations.items():
        assert sv.to_roman(n) == want, f"to_roman({n})"


def test_list_variants_puts_default_first():
    keys = sv.list_variants()
    assert keys[0] == sv.DEFAULT_VARIANT
    assert sorted(keys) == sorted(sv.VARIANTS.keys())
