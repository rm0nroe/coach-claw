"""themes.py — every theme is exactly 50 unique single-word entries."""
from __future__ import annotations

import re

import themes


def test_all_themes_have_exactly_50_entries():
    for name, ladder in themes.THEMES.items():
        assert len(ladder) == 50, f"{name} has {len(ladder)} entries, expected 50"


def test_no_duplicate_names_within_a_theme():
    for name, ladder in themes.THEMES.items():
        assert len(set(ladder)) == 50, f"{name} has duplicate level names"


def test_default_theme_preserves_v0_2_0_lader():
    """Backwards-compat: existing installs without .user_config.json read
    the default theme (`craft`), which must match the v0.2.0 ladder so
    no level-name drift on upgrade."""
    expected_first_eight = [
        "Drafter", "Iterator", "Builder", "Shipper",
        "Craftsman", "Architect", "Virtuoso", "Sensei",
    ]
    assert themes.THEME_CRAFT[:8] == expected_first_eight
    assert themes.THEME_CRAFT[-1] == "Origin"


def test_get_ladder_falls_back_to_default_on_unknown():
    assert themes.get_ladder("does-not-exist") == themes.THEMES[themes.DEFAULT_THEME]


def test_list_themes_puts_default_first():
    keys = themes.list_themes()
    assert keys[0] == themes.DEFAULT_THEME
    assert sorted(keys) == sorted(themes.THEMES.keys())


def test_theme_names_are_valid_identifiers():
    """Each level name should be a single word (no spaces, no leading
    digits) so it composes cleanly inside the statusline variants."""
    pattern = re.compile(r"^[A-Za-z][A-Za-z\-]*$")
    for name, ladder in themes.THEMES.items():
        for entry in ladder:
            assert pattern.match(entry), (
                f"{name} contains invalid name {entry!r}"
            )


def test_full_theme_lineup_is_present():
    """Pin the v0.3.0 expanded theme set: 4 abstract + 8 pop-culture = 12.
    Drop / rename / add to this assertion when the set genuinely changes."""
    expected = {
        # abstract (mythic intentionally dropped vs early v0.3.0 draft)
        "craft", "forge", "cosmic", "ocean",
        # pop-culture
        "skyrim", "marvel", "dc", "finalfantasy",
        "military", "lotr", "starwars", "hacker",
    }
    assert set(themes.THEMES.keys()) == expected, (
        f"theme set drifted from expected: "
        f"missing={expected - themes.THEMES.keys()}, "
        f"extra={themes.THEMES.keys() - expected}"
    )


def test_pop_culture_themes_exclude_franchise_coined_words():
    """Brand-safety pin. None of the franchise-coined neologisms or named
    characters listed below should appear in any pop-culture ladder. This
    is a regression guard, not exhaustive — see themes.py docstring for
    the full policy."""
    forbidden = {
        # Bethesda / Skyrim coinages
        "Dovahkiin", "Daedra", "Aedra", "Daedric", "Aedric", "Talos",
        "Alduin", "Paarthurnax", "Akatosh", "Tamriel",
        # Marvel character / trademarked group names
        "SpiderMan", "IronMan", "Wolverine", "Avengers", "Vibranium",
        "Adamantium", "OneAboveAll",
        # DC character / trademarked group names
        "Batman", "Superman", "GreenLantern", "JusticeLeague",
        "Krypton", "Kryptonian",
        # Final Fantasy specific (broader summons left in — public-domain
        # mythology — but franchise-coined character/job terms excluded)
        "OnionKnight", "Cetra", "Sephiroth", "Cloud", "Tidus",
        "Cidolfus", "BlackMage", "WhiteMage", "RedMage",
        # Tolkien coinages
        "Hobbit", "Maiar", "Valar", "Eru", "Numenor", "Mordor",
        "Shire", "Gondor", "Rohan", "Frodo", "Aragorn", "Gandalf",
        "Istari",
        # Star Wars coinages
        "Jedi", "Sith", "Padawan", "Mandalorian", "Yoda", "Vader",
        "Skywalker",  # Skywalker is a SW character; "Skywarden" is fine
        # Trademarked product names in the dev-culture theme
        "Linux", "Unix", "Microsoft", "Google", "Apple",
        "BellLabs", "Knuth", "Torvalds", "Stallman",
    }
    for theme_name, ladder in themes.THEMES.items():
        for entry in ladder:
            assert entry not in forbidden, (
                f"theme {theme_name!r} contains forbidden franchise-coined "
                f"name {entry!r} — see themes.py docstring 'Brand safety'"
            )
