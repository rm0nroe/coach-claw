"""configure.py — set / preview / wizard CLI for coach-claw config."""
from __future__ import annotations

import json
import sys

import pytest

import configure


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect config to a tmp dir via COACH_CONFIG_DIR — same pattern
    as test_user_config.py."""
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path))
    return tmp_path / ".user_config.json"


def _make_input_sequence(*answers):
    """Build a fake `input()` that returns `answers` in order. Raises if
    the wizard asks for more answers than provided — catches infinite-
    loop bugs in the validation re-prompt path."""
    it = iter(answers)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise AssertionError(
                "wizard asked for more input than the test provided "
                f"(last prompt: {prompt!r})"
            )

    return fake_input


# --- set ------------------------------------------------------------------

def test_set_writes_all_three_keys(isolated_config, capsys):
    rc = configure.main(["set", "--theme", "ocean",
                         "--statusline", "pips",
                         "--elo", "1200", "2600"])
    assert rc == 0
    payload = json.loads(isolated_config.read_text())
    assert payload["theme"] == "ocean"
    assert payload["statusline_variant"] == "pips"
    assert payload["elo_min"] == 1200
    assert payload["elo_max"] == 2600
    out = capsys.readouterr().out
    assert "saved" in out


def test_set_partial_preserves_other_keys(isolated_config):
    """Only the keys the user passes should change. Unspecified keys
    must keep whatever was already in the config."""
    # Seed with non-default values
    import user_config
    user_config.save({
        "theme": "skyrim",
        "statusline_variant": "forge",
        "elo_min": 800,
        "elo_max": 3000,
    })

    rc = configure.main(["set", "--theme", "ocean"])
    assert rc == 0

    payload = json.loads(isolated_config.read_text())
    assert payload["theme"] == "ocean"             # changed
    assert payload["statusline_variant"] == "forge"  # preserved
    assert payload["elo_min"] == 800               # preserved
    assert payload["elo_max"] == 3000              # preserved


def test_set_rejects_unknown_theme(isolated_config, capsys):
    rc = configure.main(["set", "--theme", "atlantis"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "theme" in err.lower()
    # Existing config (none in this case) must not have been corrupted.
    assert not isolated_config.exists()


def test_set_rejects_unknown_variant(isolated_config, capsys):
    rc = configure.main(["set", "--statusline", "rainbow"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "statusline_variant" in err.lower() or "statusline" in err.lower()


def test_set_rejects_invalid_elo_range(isolated_config, capsys):
    rc = configure.main(["set", "--elo", "2000", "1000"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "elo" in err.lower()


def test_set_rejects_negative_elo(isolated_config):
    """Negative ELO bounds fail at argparse parse time (before func runs)."""
    with pytest.raises(SystemExit):
        configure.main(["set", "--elo", "-100", "2800"])


def test_set_with_no_flags_complains_and_exits_nonzero(isolated_config, capsys):
    rc = configure.main(["set"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nothing to do" in err.lower() or "preview" in err.lower()


# --- preview --------------------------------------------------------------

def test_preview_lists_every_variant(isolated_config, capsys):
    rc = configure.main(["preview"])
    assert rc == 0
    out = capsys.readouterr().out
    # Every variant key must appear (bracket removed in v0.1.4).
    for variant in ["crystal", "pips", "slash", "forge"]:
        assert variant in out
    # Regression guard: bracket was removed and must not reappear in
    # the preview enumeration.
    assert "bracket" not in out


def test_preview_lists_every_theme(isolated_config, capsys):
    rc = configure.main(["preview"])
    assert rc == 0
    out = capsys.readouterr().out
    for theme in ["craft", "ocean", "skyrim", "marvel", "hacker", "lotr"]:
        assert theme in out


def test_preview_marks_current_variant_and_theme(isolated_config, capsys):
    import user_config
    user_config.save({"theme": "ocean", "statusline_variant": "pips"})

    rc = configure.main(["preview"])
    assert rc == 0
    out = capsys.readouterr().out
    # The "← current" marker should appear next to the active selections.
    # Two markers total — one variant, one theme.
    assert out.count("← current") == 2


def test_preview_pads_theme_names_consistently(isolated_config, capsys):
    """Theme-row format is `f'  {name:>13} → ...'`.

    Pinned because the slash-command skill used to have its own copy
    of this format string at width `:>7`, while configure.py used
    `:>13` — outputs drifted apart and a teammate caught the false
    'byte-equivalent' claim. After v1.0.5 the slash command delegates
    to configure.py, so configure.py is the single point of truth for
    the format width. Future intentional format changes require
    updating this test deliberately.
    """
    rc = configure.main(["preview"])
    assert rc == 0
    out = capsys.readouterr().out

    # finalfantasy is the longest theme name (12 chars). Padded to width
    # 13, it gets exactly 1 leading space inside the field — combined
    # with the 2-char literal indent, that's 3 spaces before the name.
    assert "   finalfantasy →" in out, (
        "expected theme name 'finalfantasy' right-padded to width 13; "
        "format string at coach/bin/configure.py:68 may have changed."
    )

    # dc is the shortest theme name (2 chars). Padded to width 13 it
    # gets 11 leading spaces inside the field — plus the 2-char indent
    # is 13 spaces before the name.
    assert "             dc →" in out, (
        "expected theme name 'dc' right-padded to width 13; "
        "format string at coach/bin/configure.py:68 may have changed."
    )


# --- wizard ---------------------------------------------------------------

def test_wizard_skips_on_non_tty(isolated_config, capsys, monkeypatch):
    """When stdin is not a TTY, wizard prints a pointer to `set` and
    exits 0 without reading any input."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = configure.main(["wizard"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "interactive terminal" in out.lower()
    assert "config set" in out
    # And nothing was written.
    assert not isolated_config.exists()


def test_wizard_pick_by_number_writes_config(isolated_config, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # 1) first prompt: variant. Pick "2" — second variant in VARIANTS dict
    # 2) second prompt: theme. Pick "ocean" by name.
    from statusline_variants import VARIANTS
    from themes import list_themes
    second_variant = list(VARIANTS.keys())[1]

    monkeypatch.setattr("builtins.input", _make_input_sequence("2", "ocean"))
    rc = configure.main(["wizard"])
    assert rc == 0

    payload = json.loads(isolated_config.read_text())
    assert payload["statusline_variant"] == second_variant
    assert payload["theme"] == "ocean"


def test_wizard_enter_keeps_default(isolated_config, capsys, monkeypatch):
    """Empty input on each prompt keeps the current value; the wizard
    detects 'no changes' and skips the save call."""
    import user_config
    user_config.save({"theme": "ocean", "statusline_variant": "pips"})
    pre = isolated_config.read_text()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _make_input_sequence("", ""))
    rc = configure.main(["wizard"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no changes" in out.lower()

    # File on disk byte-identical
    assert isolated_config.read_text() == pre


def test_wizard_keyboard_interrupt_does_not_save(isolated_config, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def cancel(_prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancel)
    rc = configure.main(["wizard"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cancelled" in out.lower()
    assert not isolated_config.exists()


def test_wizard_invalid_input_reprompts(isolated_config, monkeypatch):
    """First answer is bogus; second is valid. Wizard must re-prompt
    rather than crash."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # Variant prompt: "rainbow" (invalid) → re-prompt → "1" (valid)
    # Theme prompt: "ocean" (valid)
    monkeypatch.setattr(
        "builtins.input",
        _make_input_sequence("rainbow", "1", "ocean"),
    )
    rc = configure.main(["wizard"])
    assert rc == 0
    payload = json.loads(isolated_config.read_text())
    # First variant is the default; "1" picks it
    from statusline_variants import VARIANTS
    assert payload["statusline_variant"] == list(VARIANTS.keys())[0]
    assert payload["theme"] == "ocean"
