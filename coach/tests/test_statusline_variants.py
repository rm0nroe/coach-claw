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
    # bar_pct=0.30 → round(0.30*5) = 2 filled pips, 3 empty
    assert plain.startswith("●●○○○ Virtuoso")
    assert plain.endswith("↑15")


def _glyphs_with_tier(tier: str) -> sv.Glyphs:
    base = _sample_glyphs()
    return sv.Glyphs(
        level=base.level, name=base.name, elo=base.elo,
        session_xp=base.session_xp, sigil_tier=tier, bar_pct=base.bar_pct,
    )


def test_pips_filled_glyph_color_tracks_sigil_tier():
    """Filled `●` glyphs render in the sigil-tier color so the bar
    progresses bronze → silver → gold → platinum → diamond as the user
    levels up. Empty `○` glyphs stay DIM_STEEL."""
    bronze_out = sv.render("pips", _glyphs_with_tier("bronze"))
    diamond_out = sv.render("pips", _glyphs_with_tier("diamond"))
    assert sv.SIGIL_COLORS["bronze"] in bronze_out
    assert sv.SIGIL_COLORS["diamond"] in diamond_out
    assert bronze_out != diamond_out


def test_slash_variant_has_swords_sigil_and_drops_elo():
    plain = _strip_ansi(sv.render("slash", _sample_glyphs()))
    assert plain == "⚔ L7 / Virtuoso ↑15"


def test_slash_sigil_color_tracks_sigil_tier():
    """⚔ joins ◆ and ⚒ in the tier-color family — same mechanism."""
    out = sv.render("slash", _glyphs_with_tier("diamond"))
    assert sv.SIGIL_COLORS["diamond"] in out


def test_forge_variant_uses_anvil_sigil():
    plain = _strip_ansi(sv.render("forge", _sample_glyphs()))
    assert plain == "⚒ Virtuoso · L7 ↑15"


def test_bracket_variant_removed_from_registry():
    """v0.1.4: bracket dropped. `render()` falls back to crystal so
    saved configs with statusline_variant=bracket keep rendering."""
    assert "bracket" not in sv.VARIANTS
    assert len(sv.VARIANTS) == 4
    fallback = sv.render("bracket", _sample_glyphs())
    assert fallback == sv.render("crystal", _sample_glyphs())


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
