"""coach-user-prompt.py: render-shape branching by environment.

Verifies each of the six render functions emits the right shape per env:
  - terminal: blockquote `> ` shape (current default, must not regress)
  - ide:      HR-framed `---` shape with bold + code-span pills

For celebrate banners (streak/graduation/regression/levelup) the hook
now emits the **final banner markdown verbatim** — Claude reproduces it
unchanged. So these assertions pin the literal banner text, not
instruction templates.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_under_test_renderenv", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -----------------------------------------------------------------------------
# _ide_label transformations
# -----------------------------------------------------------------------------

def test_ide_label_strips_italics_and_colon(cup):
    assert cup._ide_label("*Tip:*") == "🦞 **Tip**"
    assert cup._ide_label("*Pointer:*") == "🦞 **Pointer**"


def test_ide_label_strips_leading_emoji_token(cup):
    assert cup._ide_label("*🎯 Tip:*") == "🦞 **Tip**"
    assert cup._ide_label("*✏️ Tip:*") == "🦞 **Tip**"
    assert cup._ide_label("*🧭 Heads up:*") == "🦞 **Heads up**"


def test_ide_label_strips_redundant_lobster(cup):
    """Skill labels already have 🦞; the helper drops it then re-adds the
    persona prefix so the output never has 🦞🦞."""
    assert cup._ide_label("*🦞 From Coach Claw:*") == "🦞 **From Coach Claw**"
    assert cup._ide_label("*🦞 Coach:*") == "🦞 **Coach**"


def test_ide_label_handles_multiword_label(cup):
    assert cup._ide_label("*Worth noting:*") == "🦞 **Worth noting**"
    assert cup._ide_label("*Good pattern:*") == "🦞 **Good pattern**"


def test_ide_label_always_prefixes_lobster(cup):
    """Every IDE label leads with 🦞 — universal coach signature."""
    for label in cup.WEAKNESS_LABELS + cup.STRENGTH_LABELS + cup.SKILL_LABELS:
        assert cup._ide_label(label).startswith("🦞 **")


# -----------------------------------------------------------------------------
# _xp_attribution — terminal vs IDE shape
# -----------------------------------------------------------------------------

def test_xp_attribution_terminal_uses_italics(cup):
    """Terminal shape: lines wrapped in `_..._` for theme-driven dim."""
    lines = cup._xp_attribution(
        {"kind": "weakness", "entry_id": "edits-without-testing", "clean_streak": 2,
         "reward_hint": {"action": "test_run", "xp": 2, "description": "test run"}},
        env="terminal",
    )
    assert all(line.startswith("_") and line.endswith("_") for line in lines)
    assert all("`" not in line for line in lines)  # no code spans in terminal shape


def test_xp_attribution_ide_uses_code_span_pills(cup):
    """IDE shape: lines wrapped in backticks → pill backgrounds."""
    lines = cup._xp_attribution(
        {"kind": "weakness", "entry_id": "edits-without-testing", "clean_streak": 2,
         "reward_hint": {"action": "test_run", "xp": 2, "description": "test run"}},
        env="ide",
    )
    assert all(line.startswith("`") and line.endswith("`") for line in lines)
    assert all("_" not in line for line in lines)  # no italic markers


def test_xp_attribution_ide_skill_single_line(cup):
    """Skills have only one attribution line in both envs."""
    lines = cup._xp_attribution(
        {"kind": "skill", "entry_id": "deploy-to-vercel"}, env="ide"
    )
    assert len(lines) == 1
    assert lines[0].startswith("`↑ +")
    assert "/deploy-to-vercel" in lines[0]


def test_xp_attribution_default_env_is_terminal(cup):
    """No env arg = terminal shape (backward compat)."""
    lines = cup._xp_attribution({"kind": "skill", "entry_id": "test-skill"})
    assert lines[0].startswith("_↑ +")


# -----------------------------------------------------------------------------
# _levelup_block — terminal vs IDE
# -----------------------------------------------------------------------------

def test_levelup_terminal_uses_blockquote(cup):
    block = cup._levelup_block(
        {"from": "L3 Practitioner", "to": "Reviewer", "to_idx": 3, "xp_at_levelup": 1247},
        env="terminal",
    )
    # Verbatim banner: title and stock body, both blockquote-prefixed.
    assert "> 🎉 **Level up!** You're now **L4 Reviewer**." in block
    assert "> A new craft tier unlocks at 1247 XP." in block
    assert "---" not in block


def test_levelup_ide_uses_hr_frame(cup):
    block = cup._levelup_block(
        {"from": "L3 Practitioner", "to": "Reviewer", "to_idx": 3, "xp_at_levelup": 1247},
        env="ide",
    )
    # Banner emitted at column 0 (HR-framed). Hook used to emit a
    # 4-space indented template inside an instruction block; verbatim
    # render means no leading indentation.
    assert "🎉 **LEVEL UP** — `L4 Reviewer` · `1247 XP total`" in block
    assert "A new craft tier unlocks." in block
    assert block.startswith("---\n")
    assert block.endswith("\n---")
    # Setext-H2 guard: bottom `---` must be preceded by a blank line.
    assert "\n\n---" in block
    assert "> " not in block  # no terminal blockquote signature


# -----------------------------------------------------------------------------
# _regression_block — terminal vs IDE
# -----------------------------------------------------------------------------

def test_regression_terminal_uses_blockquote(cup):
    block = cup._regression_block(
        [{"id": "edits-without-testing", "name": "Edits without testing",
          "originally_graduated_at": "2026-04-01"}],
        env="terminal",
    )
    # Slipping-surface contract (v1.0.10): canonical negative name +
    # "Bad habit returned:" heading. Positive inverse must NOT leak —
    # the user needs to recognize the slip in the language they
    # learned to fear, not a softened reframe.
    assert "> ⚠️ **Bad habit returned: edits without testing**" in block
    assert "(was graduated 2026-04-01)" in block
    assert "edits-without-testing" not in block  # slug must not leak
    assert "testing during edits" not in block  # positive frame must NOT leak
    assert "Regressed:" not in block            # old heading must NOT leak
    assert "---" not in block


def test_regression_ide_uses_hr_frame(cup):
    block = cup._regression_block(
        [{"id": "edits-without-testing", "name": "Edits without testing",
          "originally_graduated_at": "2026-04-01"}],
        env="ide",
    )
    assert "⚠️ **Bad habit returned** — `edits without testing`" in block
    assert "edits-without-testing" not in block  # slug must not leak
    assert "testing during edits" not in block  # positive frame must NOT leak
    assert "Regressed" not in block             # old heading must NOT leak
    assert block.startswith("---\n")
    assert block.endswith("\n---")
    assert "\n\n---" in block  # Setext-H2 guard


# -----------------------------------------------------------------------------
# _streak_reward_block — terminal vs IDE
# -----------------------------------------------------------------------------

def test_streak_reward_terminal_negative(cup):
    """Earning-surface contract (v1.0.10): negative-direction row uses
    `↑` (XP credit direction) and the POSITIVE INVERSE name. Canonical
    negative name must NOT leak — the user just took the positive
    action and should see it named."""
    block = cup._streak_reward_block(
        [{"id": "edits-without-testing", "name": "edits without testing",
          "direction": "negative", "streak": 3, "target": 5, "xp_awarded": 1}],
        env="terminal",
    )
    expected = "> ↑ `testing during edits` `🟢🟢🟢⚪⚪` 3/5 · `+1`"
    assert expected in block
    assert "↓" not in block                            # arrow is XP direction, always up
    assert "edits-without-testing" not in block        # slug must not leak
    assert "edits without testing" not in block        # canonical neg must NOT leak


def test_streak_reward_terminal_positive(cup):
    """Positive direction → ↑ arrow."""
    block = cup._streak_reward_block(
        [{"id": "safe-git-hygiene", "name": "safe git hygiene",
          "direction": "positive", "streak": 4, "target": 5, "xp_awarded": 2}],
        env="terminal",
    )
    expected = "> ↑ `safe git hygiene` `🟢🟢🟢🟢⚪` 4/5 · `+2`"
    assert expected in block
    assert "↓" not in block


def test_streak_reward_ide_negative(cup):
    block = cup._streak_reward_block(
        [{"id": "edits-without-testing", "name": "edits without testing",
          "direction": "negative", "streak": 3, "target": 5, "xp_awarded": 1}],
        env="ide",
    )
    expected = "↑ `testing during edits` · `🟢🟢🟢⚪⚪ 3/5` · `+1`"
    assert expected in block
    assert "↓" not in block                            # arrow is XP direction, always up
    assert "edits without testing" not in block        # canonical neg must NOT leak
    assert block.startswith("---\n")
    assert block.endswith("\n---")
    assert "\n\n---" in block  # Setext-H2 guard


def test_streak_reward_ide_positive(cup):
    block = cup._streak_reward_block(
        [{"id": "safe-git-hygiene", "name": "safe git hygiene",
          "direction": "positive", "streak": 4, "target": 5, "xp_awarded": 2}],
        env="ide",
    )
    expected = "↑ `safe git hygiene` · `🟢🟢🟢🟢⚪ 4/5` · `+2`"
    assert expected in block
    assert "↓" not in block


# -----------------------------------------------------------------------------
# _graduation_block — terminal vs IDE, both directions
# -----------------------------------------------------------------------------

def test_graduation_terminal_negative(cup):
    """Earning-surface contract (v1.0.10): both directions land on the
    word MASTERED — the glyph (⚡️ vs 🌟) distinguishes origin. Negative
    graduation uses the POSITIVE INVERSE name and "habit locked in"
    body. Canonical negative name and old "GRADUATED"/"weakness
    retired" wording must NOT leak."""
    block = cup._graduation_block(
        [{"id": "edits-without-testing", "name": "edits without testing",
          "direction": "negative", "graduated_reason": "absent-5-runs"}],
        env="terminal",
    )
    assert "> 🎓⚡️ **MASTERED: testing during edits**  `+5 XP`" in block
    assert "habit locked in" in block
    assert "removed from watchlist" in block
    assert "🌟" not in block                       # positive glyph must NOT appear
    assert "core strength" not in block           # positive body must NOT appear
    assert "GRADUATED" not in block               # old heading must NOT leak
    assert "weakness retired" not in block        # old body must NOT leak
    assert "edits-without-testing" not in block   # slug must not leak
    assert "edits without testing" not in block   # canonical neg must NOT leak
    assert "---" not in block


def test_graduation_terminal_positive(cup):
    """Positive graduation → MASTERED 🌟 shape with core-strength body.
    No NEGATIVE shape may leak (the original bug)."""
    block = cup._graduation_block(
        [{"id": "safe-git-hygiene", "name": "safe git hygiene",
          "direction": "positive", "graduated_reason": "present-5-runs"}],
        env="terminal",
    )
    assert "> 🎓🌟 **MASTERED: safe git hygiene**  `+5 XP`" in block
    assert "core strength" in block
    assert "GRADUATED" not in block  # negative shape must NOT appear
    assert "weakness retired" not in block  # the original bug — must stay gone
    assert "safe-git-hygiene" not in block  # slug must not leak
    assert "---" not in block


def test_graduation_ide_negative(cup):
    """IDE shape parallels terminal: MASTERED heading + ⚡ origin glyph
    + positive inverse name + habit-locked-in body. Old "GRADUATED" /
    "weakness retired" wording and canonical negative name must NOT
    leak (the v1.0.10 contract)."""
    block = cup._graduation_block(
        [{"id": "edits-without-testing", "name": "edits without testing",
          "direction": "negative", "graduated_reason": "absent-5-runs"}],
        env="ide",
    )
    assert "🎓 **MASTERED** ⚡ — `testing during edits` · `+5 XP`" in block
    assert "habit locked in" in block
    assert "🌟" not in block                       # positive glyph must NOT appear
    assert "GRADUATED" not in block               # old heading must NOT leak
    assert "weakness retired" not in block        # old body must NOT leak
    assert "edits without testing" not in block   # canonical neg must NOT leak
    assert block.startswith("---\n")
    assert block.endswith("\n---")
    assert "\n\n---" in block  # Setext-H2 guard


def test_graduation_ide_positive(cup):
    block = cup._graduation_block(
        [{"id": "safe-git-hygiene", "name": "safe git hygiene",
          "direction": "positive", "graduated_reason": "present-5-runs"}],
        env="ide",
    )
    assert "🎓 **MASTERED** 🌟 — `safe git hygiene` · `+5 XP`" in block
    assert "core strength" in block
    assert "GRADUATED" not in block  # negative shape must NOT appear
    assert block.startswith("---\n")
    assert block.endswith("\n---")
    assert "\n\n---" in block  # Setext-H2 guard


# -----------------------------------------------------------------------------
# Graduation full-bar color: yellow for GRADUATED ⚡️ (negative-direction,
# weakness retired), black for MASTERED 🌟 (positive-direction, strength
# locked in). The streak ladder ⚪/🔴 is reserved for active mid-streak
# attribution — graduation ceremonies get bespoke colors.
# -----------------------------------------------------------------------------

def test_curated_override_wins_over_marker_name(cup):
    """Regression: when a marker carries a `name` that differs from the
    curated entry for that slug, the resolver MUST win — earning
    surfaces consult INVERSE_OVERRIDES, slipping surfaces consult
    WORDING_OVERRIDES. The marker's `name` field is ignored in both.

    `commit-without-testing` carries marker name "commit without
    testing" (analyze.py:350); the curated negative name is the
    richer "committing without testing"; the curated positive inverse
    is "testing before committing"."""
    streak_block = cup._streak_reward_block(
        [{"id": "commit-without-testing", "name": "commit without testing",
          "direction": "negative", "streak": 3, "target": 5, "xp_awarded": 1}],
        env="terminal",
    )
    assert "testing before committing" in streak_block, (
        "streak reward banner did not resolve the positive inverse"
    )
    assert "`commit without testing`" not in streak_block, (
        "marker name leaked through despite override match"
    )
    assert "committing without testing" not in streak_block, (
        "earning surface should not show the canonical negative name"
    )

    grad_block = cup._graduation_block(
        [{"id": "commit-without-testing", "name": "commit without testing",
          "direction": "negative", "graduated_reason": "absent-5-runs"}],
        env="terminal",
    )
    assert "MASTERED: testing before committing" in grad_block, (
        "graduation banner did not resolve the positive inverse"
    )
    assert "committing without testing" not in grad_block, (
        "earning surface should not show the canonical negative name"
    )

    reg_block = cup._regression_block(
        [{"id": "commit-without-testing", "name": "commit without testing",
          "originally_graduated_at": "2026-04-01"}],
        env="terminal",
    )
    assert "Bad habit returned: committing without testing" in reg_block, (
        "regression banner did not resolve the canonical override"
    )
    assert "testing before committing" not in reg_block, (
        "slipping surface should not show the positive inverse"
    )


def test_graduation_negative_full_bar_is_yellow(cup):
    block = cup._graduation_block(
        [{"id": "edits-without-testing", "name": "edits without testing",
          "direction": "negative", "graduated_reason": "absent-5-runs"}],
        env="terminal",
    )
    assert "🟡🟡🟡🟡🟡" in block
    assert "🔴" not in block  # red is the streak ladder, not the ceremony
    assert "⚫" not in block  # black is reserved for MASTERED


def test_graduation_positive_full_bar_is_black(cup):
    block = cup._graduation_block(
        [{"id": "tests-after-edits", "name": "tests after edits",
          "direction": "positive", "graduated_reason": "present-5-runs"}],
        env="terminal",
    )
    assert "⚫️⚫️⚫️⚫️⚫️" in block
    assert "🔴" not in block
    assert "🟡" not in block  # yellow is reserved for GRADUATED


# -----------------------------------------------------------------------------
# _completion_banner — terminal vs IDE, all kinds
# -----------------------------------------------------------------------------

def test_completion_banner_terminal_skill(cup):
    block = cup._completion_banner(
        [("entry:deploy-to-vercel", {
            "kind": "skill", "entry_id": "deploy-to-vercel",
            "spec": {"action": "skill_invoke", "skill_id": "deploy-to-vercel"},
        })],
        env="terminal",
    )
    assert "> ✅ **Tip cleared** — `/deploy-to-vercel` invoked" in block
    assert "---" not in block


def test_completion_banner_ide_skill(cup):
    block = cup._completion_banner(
        [("entry:deploy-to-vercel", {
            "kind": "skill", "entry_id": "deploy-to-vercel",
            "spec": {"action": "skill_invoke", "skill_id": "deploy-to-vercel"},
        })],
        env="ide",
    )
    assert "  ---" in block
    assert "✅ **Tip cleared** — `/deploy-to-vercel` invoked" in block
    assert "\n\n  ---" in block  # Setext-H2 guard


def test_completion_banner_ide_weakness(cup):
    """Earning-surface contract (v1.0.10): weakness tip-completion acks
    fire when the user just took the positive action — name the
    action, not the bad habit. Canonical negative name must NOT
    leak."""
    block = cup._completion_banner(
        [("entry:edits-without-testing", {
            "kind": "weakness", "entry_id": "edits-without-testing",
            "clean_streak": 2,
            "spec": {"action": "test_run", "xp": 2, "description": "test run"},
        })],
        env="ide",
    )
    assert "  ---" in block
    assert "✅ **Tip cleared** — `testing during edits`" in block
    assert "edits-without-testing" not in block        # slug must not leak
    assert "`edits without testing`" not in block      # canonical neg must NOT leak
    assert "`+2 XP banked`" in block
    assert "`streak 🟢🟢⚪⚪⚪`" in block
    # v0.1.19+: internal /coach-insights mechanic must NOT leak to user
    # — system is automatic by nature; bar speaks for itself.
    assert "/coach-insights" not in block
    assert "advances" not in block


def test_completion_banner_ide_strength(cup):
    block = cup._completion_banner(
        [("entry:tests-after-edits", {
            "kind": "strength", "entry_id": "tests-after-edits",
            "positive_streak": 2,
            "spec": {"action": "test_run", "xp": 2, "description": "test run"},
        })],
        env="ide",
    )
    assert "  ---" in block
    assert "💪 **Strength reinforced** — `testing after edits`" in block
    assert "tests-after-edits" not in block  # slug must not leak
    assert "`strength streak 🟢🟢⚪⚪⚪`" in block


# -----------------------------------------------------------------------------
# All renderers preserve current default behavior when env arg omitted
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda c: c._levelup_block({"from": "L3", "to": "Reviewer", "to_idx": 3, "xp_at_levelup": 100}),
    lambda c: c._regression_block([{"id": "p1", "name": "P", "originally_graduated_at": "2026-01-01"}]),
    lambda c: c._streak_reward_block([{"id": "p1", "name": "P", "streak": 1, "target": 5, "xp_awarded": 1}]),
    lambda c: c._graduation_block([{"id": "p1", "name": "P", "direction": "negative", "graduated_reason": "x"}]),
    lambda c: c._completion_banner([("e:p1", {"kind": "skill", "entry_id": "p1",
                                              "spec": {"action": "skill_invoke", "skill_id": "p1"}})]),
])
def test_omitted_env_arg_preserves_terminal_shape(cup, call):
    """No env arg = terminal shape (backward compat invariant)."""
    block = call(cup)
    assert "> " in block  # terminal blockquote present
    # IDE-only signature characters absent
    assert "    ---" not in block
    assert "  ---\n" not in block
