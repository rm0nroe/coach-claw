#!/usr/bin/env python3
"""
Skill inventory builder.

Scans ~/.claude/skills/*/SKILL.md (user-level only, not plugin-bundled —
those are already too noisy and mostly auto-triggered), reads the frontmatter
description, then returns a JSON list of skills the user has NOT invoked in
this window. The result is written to profile.yaml as a reference snapshot
for the SessionStart hook to surface as present-tense suggestions.

Usage:
  skill_inventory.py --used-json <path>   # JSON dict {skill_id: count}
Prints JSON array of {id, description, last_invoked, projects} to stdout.

`projects` is read from the optional SKILL.md frontmatter field of the
same name. When set, the hook treats the skill as project-scoped — it
only fires when the current cwd's project matches one of the declared
entries. Untagged skills (empty list) behave globally as before.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKILLS_DIR = Path.home() / ".claude" / "skills"

# Skip self-referential and trivial-meta skills
EXCLUDE_IDS = {
    "coach", "insights",       # self (don't recommend coach to itself)
    "checkpoint", "load", "sessions",  # session lifecycle — auto-used
}

MAX_HINTS = 20

# Inference threshold: number of invocations in a project before that
# project counts as an "observed" home for a skill. ≥2 protects against
# one-off experimental invocations from contaminating the inferred
# scope. Tunable here if real-world data argues for a stricter or
# looser bar; everywhere else just consumes _infer_projects().
INFER_THRESHOLD = 2


def _parse_frontmatter_text(text: str) -> dict:
    """Extract YAML frontmatter from a SKILL.md body. Returns an empty
    dict on any failure — frontmatter is best-effort and the /coach-insights
    cron path must never crash on a malformed SKILL.md file."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml
    except Exception:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_frontmatter(md_path: Path) -> dict:
    try:
        text = md_path.read_text()
    except Exception:
        return {}
    return _parse_frontmatter_text(text)


def _infer_projects(
    skill_id: str,
    by_project: dict,
    threshold: int = INFER_THRESHOLD,
) -> list[str]:
    """Infer a skill's project scope from rolling invocation history.

    ``by_project`` is the effective accumulator
    ``{project: {skill_id: count}}`` (existing profile state + this
    run's delta).

    Rule:
      - "Observed" projects = where this skill has been invoked
        ≥``threshold`` times.
      - If observed in ≥2 projects → return ``[]`` (cross-cutting;
        graduates to global behavior identical to today's untagged
        skills). Auto-handles tools like /design or /capability-loop
        that legitimately span projects.
      - If observed in exactly 1 project → return ``[that_project]``.
      - Otherwise → ``[]`` (cold-start: not enough signal to claim
        scope yet; the hook's untagged-skill rules still apply).

    Frontmatter ``projects:`` always supersedes inference — see
    main(). This function is purely advisory.
    """
    if not isinstance(by_project, dict):
        return []
    observed: list[str] = []
    for proj, skills in by_project.items():
        if not isinstance(skills, dict):
            continue
        try:
            count = int(skills.get(skill_id, 0))
        except (TypeError, ValueError):
            continue
        if count >= threshold:
            observed.append(str(proj))
    if len(observed) == 1:
        return observed
    return []   # 0 → cold-start, ≥2 → graduated to global


def _coerce_projects(raw) -> list[str]:
    """Normalize the frontmatter `projects:` field into a list of
    non-empty strings. Accepts list-form (normal), scalar-string
    (single-project shorthand), anything else → empty list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for p in raw:
            if isinstance(p, str) and p.strip():
                out.append(p.strip())
        return out
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--used-json", type=Path, default=None)
    ap.add_argument(
        "--skills-by-project", type=Path, default=None,
        help="Optional JSON {project: {skill_id: count}} of effective "
             "rolling invocation history (profile state + this run's "
             "delta). When provided, skills without an explicit "
             "frontmatter `projects:` field get scope inferred from "
             "history (≥2 invocations in 1 project → tagged; ≥2 "
             "projects observed → graduated to global; otherwise "
             "untagged).")
    args = ap.parse_args()

    used = {}
    if args.used_json and args.used_json.exists():
        try:
            used = json.loads(args.used_json.read_text()) or {}
        except Exception:
            used = {}

    by_project: dict = {}
    if args.skills_by_project and args.skills_by_project.exists():
        try:
            raw = json.loads(args.skills_by_project.read_text()) or {}
            if isinstance(raw, dict):
                by_project = raw
        except Exception:
            by_project = {}

    hints: list[dict] = []
    if not SKILLS_DIR.exists():
        print("[]")
        return

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        sid = skill_dir.name
        if sid in EXCLUDE_IDS:
            continue
        # Skip if user invoked it in this window
        if int(used.get(sid, 0)) > 0:
            continue
        fm = parse_frontmatter(skill_md)
        desc_raw = fm.get("description")
        desc = (desc_raw.strip() if isinstance(desc_raw, str) else "")
        if not desc:
            continue
        # Truncate long descriptions to keep injection context small
        desc_short = desc[:220].rstrip()
        if len(desc) > 220:
            desc_short += "..."
        # Frontmatter `projects:` is authoritative when present at all;
        # otherwise we infer from rolling invocation history. The key
        # distinction: `projects: []` (explicitly empty) is a USER
        # DECLARATION of "this skill is cross-project / global" and must
        # NOT silently fall through to inference, which could re-tag
        # the skill from history and reverse the user's intent.
        # `fm.get("projects")` returns None only when the key is
        # genuinely absent (PyYAML's safe_load yields None for
        # `projects:` with no value too — treated as absent here, since
        # the key with no value carries no semantic content).
        raw_projects = fm.get("projects")
        if raw_projects is None:
            projects = _infer_projects(sid, by_project)
        else:
            projects = _coerce_projects(raw_projects)
        hints.append({
            "id": sid,
            "description": desc_short,
            "last_invoked": None,
            "projects": projects,
        })

    hints = hints[:MAX_HINTS]
    print(json.dumps(hints, indent=2))


if __name__ == "__main__":
    main()
