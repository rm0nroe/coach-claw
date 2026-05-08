"""Plugin skills must reference `/coach-claw:` namespaced commands, not
the bare CLI shortforms.

Plugin skills are namespaced by Claude Code (per the plugin model spec).
The bare names `/coach`, `/coach-insights`, `/config` only work in the
npm CLI distribution; in the plugin distribution they'd be misleading
(users would type them and Claude Code would respond "no such command")
and self-references inside SKILL.md would silently rot.

This test catches that at CI time. CLI skills under `skills/` are NOT
checked — they intentionally use the bare names.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_SKILLS = REPO_ROOT / "plugin" / "skills"


# Match a slash command at a word boundary, NOT preceded by a colon
# (so `/coach-claw:coach` doesn't false-match as `/coach`).
_BARE_COACH = re.compile(r"(?<![:\w])/coach(?![-:\w])")
_BARE_COACH_INSIGHTS = re.compile(r"(?<![:\w])/coach-insights\b")
_BARE_CONFIG = re.compile(r"(?<![:\w])/config\b")


def _read_skill(name: str) -> str:
    path = PLUGIN_SKILLS / name / "SKILL.md"
    assert path.exists(), f"missing plugin skill: {path}"
    return path.read_text()


def test_coach_skill_uses_namespaced_self_reference():
    body = _read_skill("coach")
    bare_matches = _BARE_COACH.findall(body)
    assert bare_matches == [], (
        f"plugin/skills/coach/SKILL.md references bare `/coach` "
        f"({len(bare_matches)} hit(s)); use `/coach-claw:coach` so the "
        f"plugin's actual invocation matches the docs."
    )


def test_coach_insights_skill_uses_namespaced_self_reference():
    body = _read_skill("coach-insights")
    bare_matches = _BARE_COACH_INSIGHTS.findall(body)
    assert bare_matches == [], (
        f"plugin/skills/coach-insights/SKILL.md references bare "
        f"`/coach-insights`; use `/coach-claw:coach-insights`."
    )


def test_config_skill_uses_namespaced_self_reference():
    body = _read_skill("config")
    bare_matches = _BARE_CONFIG.findall(body)
    assert bare_matches == [], (
        f"plugin/skills/config/SKILL.md references bare `/config`; "
        f"use `/coach-claw:config`."
    )


def test_skills_use_plugin_root_for_code_paths():
    """CODE references should use ${CLAUDE_PLUGIN_ROOT}/bin/, not the
    CLI's ~/.claude/coach/bin/. STATE references like
    ~/.claude/coach/profile.yaml are OK — that path is shared between
    distributions and we don't want to fork it."""
    for skill in ("coach", "coach-insights", "config"):
        body = _read_skill(skill)
        assert "~/.claude/coach/bin/" not in body, (
            f"plugin/skills/{skill}/SKILL.md references CLI-only path "
            f"~/.claude/coach/bin/; use ${{CLAUDE_PLUGIN_ROOT}}/bin/ "
            f"so the path resolves to the installed plugin's code."
        )
