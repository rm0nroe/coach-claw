"""Pin the BSD `mktemp` template gotcha.

`mktemp /tmp/foo-XXXXXX.json` on BSD (macOS default) creates a file named
literally `/tmp/foo-XXXXXX.json` — the `X`s are not replaced because BSD
mktemp won't substitute Xs that aren't at the end of the template. The
second run then silently fails because the file already exists. GNU
mktemp tolerates this. We had this bug in `skills/coach-insights/SKILL.md`
(then `skills/insights/SKILL.md`) pre-v0.2.0 and it broke the on-demand
analyzer flow for macOS users.

This test scans the bundle's own shell scripts and skill files for any
`mktemp` invocation whose template has a suffix after the `X`s. It is
careful to only scan files this bundle owns — running from
`~/.claude/coach/tests/` post-install, the broader `~/.claude/skills/`
directory contains skills from other plugins that this project does not
ship and should not police.

CLAUDE.md references the bad pattern in *prose* (to explain the
gotcha), so we intentionally don't scan pure prose docs.
"""
from __future__ import annotations

import re
from pathlib import Path

# Match `mktemp ` followed by anything, ending with `XXXXXX.<word>` BEFORE
# the closing whitespace, paren, or quote — i.e. an mktemp template that
# has a suffix after the X-block. This is what BSD won't substitute.
BROKEN_RE = re.compile(r"\bmktemp\s+[^\n)`'\"]*X{4,}\.[A-Za-z0-9]+")

# Skills the coach bundle owns — these are the only `skills/<name>/SKILL.md`
# files we should police. Other skills under `~/.claude/skills/` belong to
# unrelated plugins and may have their own conventions.
OWNED_SKILLS = ("coach-insights", "coach", "config")


def _scan_paths() -> list[Path]:
    """Return absolute paths to scan, working in both layouts:

    Bundle layout (running from repo checkout):
        REPO_ROOT/coach/tests/test_no_broken_mktemp.py
        REPO_ROOT/coach/bin/*.sh
        REPO_ROOT/skills/{coach-insights,coach,config}/SKILL.md
        REPO_ROOT/install.sh, install-launchd.sh

    Install layout (running from ~/.claude/coach/tests/ after ./install.sh):
        ~/.claude/coach/tests/test_no_broken_mktemp.py
        ~/.claude/coach/bin/*.sh
        ~/.claude/skills/{coach-insights,coach,config}/SKILL.md
        (install.sh / install-launchd.sh do NOT exist post-install)
    """
    test_file = Path(__file__).resolve()
    coach_root = test_file.parent.parent          # the `coach/` directory
    above = coach_root.parent                     # bundle root OR ~/.claude

    paths: list[Path] = []
    paths.extend(sorted(coach_root.glob("bin/*.sh")))
    for name in ("install.sh", "install-launchd.sh"):
        p = above / name
        if p.is_file():
            paths.append(p)
    for skill in OWNED_SKILLS:
        p = above / "skills" / skill / "SKILL.md"
        if p.is_file():
            paths.append(p)
    return paths


def test_no_broken_mktemp_templates_in_scripts():
    paths = _scan_paths()
    assert paths, (
        "scan resolved zero candidate paths — fixture broken; "
        "expected at least coach/bin/*.sh"
    )
    offenders: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if BROKEN_RE.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found `mktemp` template(s) with a suffix after the X-block.\n"
        "On BSD/macOS this creates a literal `XXXXXX...` filename and "
        "the second run silently fails. Move the suffix off the template "
        "(callers don't rely on the extension).\n\n"
        + "\n".join(offenders)
    )
