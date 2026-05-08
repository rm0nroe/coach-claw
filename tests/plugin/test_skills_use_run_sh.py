"""Contract: plugin SKILL.md files must route Python invocations through
`${CLAUDE_PLUGIN_ROOT}/bin/run.sh`, never bare `python3 ${CLAUDE_PLUGIN_ROOT}/bin/...`.

`run.sh` provisions the per-plugin PyYAML venv on first invocation
and execs the wrapped Python under that venv. Skills that bypass
run.sh fall back to system `python3`, which on a fresh
plugin-only box has no PyYAML — `import yaml` fails and the skill
silently no-ops or errors.

This bug was filed as a discovered gap during 2026-05-09 e2e
validation and fixed in v0.1.2; this test exists to keep it fixed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_SKILLS = REPO_ROOT / "plugin" / "skills"


# Pattern: `python3 ${CLAUDE_PLUGIN_ROOT}/bin/X.py` (with or without a leading
# dollar in env-var form). Matches both `python3` and `python3 -` (heredoc).
# Allowed: `${CLAUDE_PLUGIN_ROOT}/bin/run.sh ${CLAUDE_PLUGIN_ROOT}/bin/X.py`.
_BAD_BARE_PYTHON = re.compile(
    r"python3\s+(?:-\s+)?\$\{CLAUDE_PLUGIN_ROOT\}/bin/"
)
# heredoc form: `python3 - <<PY` or `python3 - "$VAR" <<PY`
_BAD_HEREDOC_PYTHON = re.compile(
    r"^[^#\n]*python3\s+-\s+(?!-)",  # python3 - (not --) — heredoc form
    re.MULTILINE,
)


def _read_skill(name: str) -> str:
    return (PLUGIN_SKILLS / name / "SKILL.md").read_text()


def _check(skill_name: str):
    body = _read_skill(skill_name)
    bare = _BAD_BARE_PYTHON.findall(body)
    assert bare == [], (
        f"plugin/skills/{skill_name}/SKILL.md invokes "
        f"`python3 ${{CLAUDE_PLUGIN_ROOT}}/bin/...` directly "
        f"({len(bare)} hit(s)); route through "
        f"`${{CLAUDE_PLUGIN_ROOT}}/bin/run.sh` so the venv's PyYAML "
        f"is available."
    )
    heredoc = _BAD_HEREDOC_PYTHON.findall(body)
    assert heredoc == [], (
        f"plugin/skills/{skill_name}/SKILL.md uses bare `python3 -` "
        f"heredoc ({len(heredoc)} hit(s)); use "
        f"`${{CLAUDE_PLUGIN_ROOT}}/bin/run.sh -` instead."
    )


def test_coach_skill_uses_run_sh():
    _check("coach")


def test_config_skill_uses_run_sh():
    _check("config")


def test_switch_skill_uses_run_sh():
    _check("switch")


def test_coach_insights_skill_can_use_bare_shell_script():
    """`coach-insights` invokes `${CLAUDE_PLUGIN_ROOT}/bin/insights-llm.sh`
    directly (not through run.sh), and that's intentional —
    insights-llm.sh self-detects CLAUDE_PLUGIN_DATA and prepends the
    venv to PATH at startup, so its internal `python3` calls resolve
    PyYAML from the venv. This contract is also pinned in
    `coach/bin/insights-llm.sh` itself; see test_insights_llm.py."""
    body = _read_skill("coach-insights")
    bare = _BAD_BARE_PYTHON.findall(body)
    # Allowed for this skill specifically — the inner shell script
    # handles venv routing itself.
    assert bare == [], (
        "coach-insights skill should invoke insights-llm.sh, NOT "
        "python3 directly (the shell script handles its own venv "
        "routing). Found bare-python invocations: " + repr(bare)
    )
